"""Triage Line confirmation-safety regression tests (audit E-01 .. E-07).

Every probe from docs/audit/FULL_AUDIT_2026-09-25.md section 5 is pinned here, driven through the
real TriageCallSession (legacy dialogue -> deliberation -> commit state machine) with the legacy
mock STT/TTS/audio providers.

Run: python3 livekit_agent/adapter_tests/test_8_triage_confirmation_safety.py
"""
import asyncio
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "legacy"))

from livekit_agent.triage_brain import (  # noqa: E402
    CONFIRM, CORRECTION, DECLINE, OTHER, TriageCallSession, classify_confirmation)
from core.commit.state_machine import CommitState  # noqa: E402
from providers.mock.mock_audio_io import MockAudioIO  # noqa: E402
from providers.mock.mock_stt import MockSTT  # noqa: E402
from providers.mock.mock_tts import MockTTS  # noqa: E402


def new_session(cid="call-safety"):
    return TriageCallSession(call_id=cid, stt=MockSTT(), tts=MockTTS(), audio_io=MockAudioIO())


async def pending_tow(s, where="Highway 9"):
    await s.on_final_transcript(f"My car broke down near {where}.")
    assert s.brain.pending_action_id is not None, "expected a pending tow proposal"
    return s.brain.pending_action_id


def test_classifier_table():
    table = {
        "yes": CONFIRM, "Yes please, go ahead.": CONFIRM, "yeah do it": CONFIRM,
        "Do not confirm": DECLINE, "No, do not send it": DECLINE, "not yet": DECLINE, "stop": DECLINE,
        "Yes, but actually I am on Highway 12": CORRECTION, "actually I'm at exit 4": CORRECTION,
        "Yesterday I called": OTHER, "yessir the engine is smoking": OTHER,
    }
    for text, want in table.items():
        got = classify_confirmation(text)
        assert got == want, f"{text!r}: expected {want}, got {got}"


async def e01_do_not_confirm():
    s = new_session("e01")
    aid = await pending_tow(s)
    await s.on_final_transcript("Do not confirm.")
    assert not s.dispatch_log, "E-01: 'Do not confirm' dispatched a tow"
    assert s.commit.get(aid).current_state == CommitState.ABORTED


async def e02_yes_but_correction():
    s = new_session("e02")
    aid = await pending_tow(s)
    await s.on_final_transcript("Yes, but actually I am on Highway 12.")
    assert not s.dispatch_log, "E-02: mixed 'yes but actually' dispatched to the stale location"
    assert s.commit.get(aid).is_terminal, "stale Highway 9 action must be aborted"
    new = s.commit.get(s.brain.pending_action_id)
    assert "12" in str(new.action_payload.get("location")), new.action_payload
    await s.on_final_transcript("Yes.")
    assert len(s.dispatch_log) == 1 and "12" in str(s.dispatch_log[0].payload.get("location"))


async def e03_yesterday_is_not_yes():
    s = new_session("e03")
    await pending_tow(s)
    await s.on_final_transcript("Yesterday I called about this too.")
    assert not s.dispatch_log, "E-03: 'yesterday' matched as 'yes' and dispatched"


async def e04_no_duplicate_dispatch():
    s = new_session("e04")
    await pending_tow(s)
    await s.on_final_transcript("Yes.")
    assert len(s.dispatch_log) == 1
    await s.on_final_transcript("My car broke down near Highway 9.")
    await s.on_final_transcript("Yes.")
    assert len(s.dispatch_log) == 1, f"E-04: same incident dispatched {len(s.dispatch_log)} times"


async def e05_negated_hazard_is_not_emergency():
    s = new_session("e05")
    await s.on_final_transcript("My car broke down near Highway 9, there is no fire and nobody is injured.")
    rec = s.commit.get(s.brain.pending_action_id)
    assert rec.action_type == "dispatch_tow", f"E-05: negated hazard escalated as {rec.action_type}"
    s2 = new_session("e05b")
    await s2.on_final_transcript("My car broke down near Highway 9 and there's smoke coming out.")
    assert s2.commit.get(s2.brain.pending_action_id).action_type == "escalate_emergency"


async def e06_yes_after_teardown():
    s = new_session("e06")
    await pending_tow(s)
    await s.teardown()
    await s.on_final_transcript("Yes.")        # must not raise InvalidTransitionError
    assert not s.dispatch_log, "E-06: confirmation accepted after teardown"
    assert all(a.is_terminal for a in s.commit.list_for_call("e06"))


async def e07_explicit_decline_still_works():
    for text in ("No, do not send it.", "Not yet."):
        s = new_session("e07")
        aid = await pending_tow(s)
        await s.on_final_transcript(text)
        assert not s.dispatch_log and s.commit.get(aid).current_state == CommitState.ABORTED, text


async def main():
    test_classifier_table()
    for probe in (e01_do_not_confirm, e02_yes_but_correction, e03_yesterday_is_not_yes,
                  e04_no_duplicate_dispatch, e05_negated_hazard_is_not_emergency,
                  e06_yes_after_teardown, e07_explicit_decline_still_works):
        await probe()
        print(f"  ok  {probe.__name__}")
    print("PASS: test_8_triage_confirmation_safety")


if __name__ == "__main__":
    asyncio.run(main())
