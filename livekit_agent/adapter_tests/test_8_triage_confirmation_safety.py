"""Triage Line confirmation-safety regression tests (audit E-01 .. E-07).

Every probe from docs/archive/audit/FULL_AUDIT_2026-09-25.md section 5 is pinned here, driven through the
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


def _spoken_text(self) -> list[str]:
    for owner in (getattr(self, "_rec_tts", None),
                  getattr(getattr(self, "dialogue", None), "tts", None)):
        if owner is not None and hasattr(owner, "spoken"):
            return list(owner.spoken or [])
    return []


TriageCallSession._spoken = _spoken_text


class _RecordingTTS(MockTTS):
    """MockTTS that keeps the spoken text, so tests can assert on what the caller hears."""

    def __init__(self):
        super().__init__()
        self.spoken: list[str] = []

    async def start_speaking(self, text: str) -> None:
        self.spoken.append(text or "")
        await super().start_speaking(text)


def new_session(cid="call-safety"):
    rec = _RecordingTTS()
    s = TriageCallSession(call_id=cid, stt=MockSTT(), tts=rec, audio_io=MockAudioIO())
    s._rec_tts = rec
    return s


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


# --------------------------------------------------------------------------- audit regression cases
# Collected by pytest (the e01_..e07 probes above only run via main()); these cover the
# third-party audit's reproduced triage defects.

def test_affirmative_with_new_location_never_confirms_stale_proposal():
    asyncio.run(_t_affirmative_with_new_location())


async def _t_affirmative_with_new_location():
    """'Yes, I am on Highway 12' while a Highway 9 tow is pending -> re-propose, never dispatch."""
    s = new_session("r1")
    await pending_tow(s, "Highway 9")
    await s.on_final_transcript("Yes, I am on Highway 12.")
    assert not s.dispatch_log, "confirmed the stale Highway 9 proposal"
    rec = s.commit.get(s.brain.pending_action_id)
    assert "12" in str(rec.action_payload.get("location")), rec.action_payload


def test_affirmative_with_new_hazard_never_confirms_standard_tow():
    asyncio.run(_t_affirmative_with_new_hazard())


async def _t_affirmative_with_new_hazard():
    """'Okay, the engine is smoking' is not consent for a standard tow."""
    s = new_session("r2")
    await pending_tow(s, "Highway 9")
    await s.on_final_transcript("Okay, the engine is smoking.")
    assert not s.dispatch_log, "confirmed a standard tow while an emergency was reported"


def test_incident_dedupes_across_wording_changes():
    asyncio.run(_t_incident_dedupes())


async def _t_incident_dedupes():
    """The same place phrased differently must still dedupe to one dispatch."""
    s = new_session("r3")
    await pending_tow(s, "Highway 9")
    await s.on_final_transcript("Yes.")
    await s.on_final_transcript("I broke down near Highway 9 again, my sedan is blue.")
    await s.on_final_transcript("Yes.")
    assert len(s.dispatch_log) == 1, [d.payload for d in s.dispatch_log]


def test_location_is_not_taken_from_unrelated_numbers():
    asyncio.run(_t_location_not_from_numbers())


async def _t_location_not_from_numbers():
    """'12 volts' is not a location; the snippet stops at the sentence boundary."""
    from core.deliberation.intents import _extract_location_snippet, _has_location
    assert _extract_location_snippet("My car broke down near Highway 9. My battery is 12 volts.") == "Highway 9"
    assert not _has_location("my battery is 12 volts")


def test_corrected_hazard_is_not_active():
    asyncio.run(_t_corrected_hazard())


async def _t_corrected_hazard():
    """A later negation of the same hazard supersedes the earlier report."""
    from core.deliberation.intents import _find_emergency_reason
    assert _find_emergency_reason("there is a fire in the engine, actually no wait, there is no fire") is None
    assert _find_emergency_reason("the engine is on fire") == "fire"
    assert _find_emergency_reason("the engine is smoking") == "smoke"


def test_new_emergency_reopens_a_closed_case():
    asyncio.run(_t_new_emergency_reopens())


async def _t_new_emergency_reopens():
    s = new_session("r6")
    await pending_tow(s, "Highway 9")
    await s.on_final_transcript("That's all, thanks.")
    await s.on_final_transcript("The engine is on fire.")
    spoken = " ".join(s._spoken())
    assert "ended" not in spoken.lower(), f"a new emergency was refused by the old closure: {spoken!r}"
    assert not s.brain.closed, "a new emergency must reopen the case"


def test_emergency_guidance_comes_before_location_gathering():
    asyncio.run(_t_emergency_guidance_first())


async def _t_emergency_guidance_first():
    """An emergency must not be gated behind the location question."""
    s = new_session("r7")
    await s.on_final_transcript("The engine is on fire.")
    assert not s.dispatch_log
    spoken = " ".join(s._spoken())
    assert "emergency" in spoken.lower(), spoken


def test_closure_resolves_a_pending_action():
    """Closing the case must leave no action in 'pending confirmation' (audit E-06)."""
    asyncio.run(_t_closure_resolves_pending())


async def _t_closure_resolves_pending():
    s = new_session("r8")
    await pending_tow(s, "Highway 9")
    aid = s.brain.pending_action_id
    await s.on_final_transcript("That's all, thanks.")
    assert s.commit.get(aid).current_state != CommitState.PENDING_CONFIRMATION, \
        "a pending tow was left open after the case was closed"
    assert s.brain.closed, "closure was not applied"
