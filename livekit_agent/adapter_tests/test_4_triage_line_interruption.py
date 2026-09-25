"""Triage Line extension: interruption/correction, confirmation safety,
and disconnect teardown, driven end-to-end through the SAME legacy
dialogue -> deliberation -> commit stack the LiveKit entrypoint uses
(via livekit_agent/triage_brain.py). No LiveKit/OpenAI network or
credentials needed -- caller audio is replaced by legacy's own
MockSTT/MockTTS/MockAudioIO, exactly as legacy/tests already does.

Scenario (matches EXTENSION_DEMO_TRANSCRIPT.md):
  1. Caller: "My car broke down near Highway 9."
     -> agent proposes dispatch_tow(location=Highway 9), asks to confirm.
  2. Caller interrupts mid-confirmation-prompt with a correction:
     "Actually, I'm on Highway 12."
     -> stale Highway-9 action is aborted (never confirmed, never dispatched)
     -> a new action for Highway 12 is proposed and pending confirmation.
  3. Confirmation safety: assert the Highway-12 action is NOT finalized yet.
  4. Caller confirms: "Yes, go ahead."
     -> action FINALIZED, dispatch logged for Highway 12 only.
  5. Teardown: force_resolve_pending called; no action left non-terminal.

Run: python3 livekit_agent/adapter_tests/test_4_triage_line_interruption.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from livekit_agent.triage_brain import TriageCallSession  # noqa: E402

_LEGACY_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "legacy")
if _LEGACY_ROOT not in sys.path:
    sys.path.insert(0, _LEGACY_ROOT)

from core.commit.state_machine import CommitState  # noqa: E402
from providers.mock.mock_audio_io import MockAudioIO  # noqa: E402
from providers.mock.mock_stt import MockSTT  # noqa: E402
from providers.mock.mock_tts import MockTTS  # noqa: E402


async def main():
    stt = MockSTT()
    tts = MockTTS()
    audio_io = MockAudioIO()
    session = TriageCallSession(call_id="call-triage-1", stt=stt, tts=tts, audio_io=audio_io)

    # --- 1. caller reports the breakdown with a location ---
    await session.on_final_transcript("My car broke down near Highway 9.")
    assert session.brain.pending_action_id is not None, "expected a proposed action awaiting confirmation"
    highway9_action_id = session.brain.pending_action_id
    highway9_action = session.commit.get(highway9_action_id)
    assert highway9_action.action_payload.get("location", "").lower().startswith("highway 9"), (
        f"expected Highway 9 in first proposal, got {highway9_action.action_payload}"
    )
    assert highway9_action.current_state == CommitState.PENDING_CONFIRMATION
    print("Step 1 OK — proposed:", highway9_action.action_type, highway9_action.action_payload, highway9_action.current_state)

    # Agent is now "speaking" the confirmation prompt (dialogue engine moved
    # to AGENT_SPEAKING as part of _produce_agent_response's TTS call).
    assert session.dialogue.turn_state.state.value == "agent_speaking"

    # --- 2. caller barges in with a correction while the agent is mid-prompt ---
    audio_io._agent_playing = True
    audio_io._caller_speaking = True  # caller starts talking over the agent
    barge_in = await session.check_for_barge_in()
    assert barge_in is not None, "expected check_for_barge_in to detect the barge-in"
    assert tts.was_interrupted(), "TTS should have been stopped mid-utterance"

    await session.on_final_transcript("Actually, I'm on Highway 12.")

    # The stale Highway-9 action must be cancelled/aborted, never confirmed.
    highway9_action = session.commit.get(highway9_action_id)
    assert highway9_action.current_state == CommitState.ABORTED, (
        f"expected stale Highway-9 action aborted, got {highway9_action.current_state}"
    )
    assert highway9_action.aborted_reason == "superseded_by_redeliberation"
    print("Step 2 OK — stale Highway 9 action aborted:", highway9_action.current_state, highway9_action.aborted_reason)

    # A new action for Highway 12 must now be pending.
    highway12_action_id = session.brain.pending_action_id
    assert highway12_action_id is not None and highway12_action_id != highway9_action_id
    highway12_action = session.commit.get(highway12_action_id)
    assert "highway 12" in highway12_action.action_payload.get("location", "").lower(), (
        f"expected Highway 12 in re-deliberated proposal, got {highway12_action.action_payload}"
    )
    print("Step 2b OK — new proposal:", highway12_action.action_type, highway12_action.action_payload)

    # --- 3. confirmation safety: nothing finalized automatically ---
    assert highway12_action.current_state == CommitState.PENDING_CONFIRMATION, (
        "action must not auto-finalize without an explicit confirm()"
    )
    assert not any(e.payload.get("location", "").lower().startswith("highway 9") for e in session.dispatch_log), (
        "must never have dispatched based on the stale Highway 9 location"
    )
    print("Step 3 OK — no auto-finalization; dispatch log so far:", session.dispatch_log)

    # --- 4. caller explicitly confirms ---
    await session.on_final_transcript("Yes, go ahead.")
    highway12_action = session.commit.get(highway12_action_id)
    assert highway12_action.current_state == CommitState.FINALIZED
    assert len(session.dispatch_log) == 1
    assert "highway 12" in session.dispatch_log[0].payload.get("location", "").lower()
    print("Step 4 OK — finalized and dispatched:", session.dispatch_log[0])

    # --- 5. terminal-state guarantee across the whole call ---
    for action in session.commit.list_for_call(session.call_id):
        assert action.is_terminal, f"action {action.action_id} left non-terminal mid-call: {action.current_state}"
    print("Step 5a OK — every action so far is terminal:", [a.current_state for a in session.commit.list_for_call(session.call_id)])

    # --- 6. disconnect teardown: nothing is ever left non-terminal ---
    # Start a second, never-answered proposal, then simulate the caller
    # hanging up mid-confirmation to exercise force_resolve_pending().
    await session.on_final_transcript("Also my car is a blue sedan.")  # no new location -> re-affirms breakdown intent
    dangling_id = session.brain.pending_action_id
    if dangling_id is not None:
        dangling = session.commit.get(dangling_id)
        assert not dangling.is_terminal, "test setup expected a live pending action before teardown"

    resolved = await session.teardown(reason="caller_disconnected")
    for action in session.commit.list_for_call(session.call_id):
        assert action.is_terminal, f"action {action.action_id} left non-terminal after teardown: {action.current_state}"
    print("Step 6 OK — force_resolve_pending on teardown resolved:", [(r.action_id, r.current_state, r.aborted_reason) for r in resolved])

    print("\nPASS: test_4_triage_line_interruption")


if __name__ == "__main__":
    asyncio.run(main())
