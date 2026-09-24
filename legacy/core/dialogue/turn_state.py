"""Turn-state model for the dialogue engine.

Tracks whose turn it conceptually is and whether an interruption is in
progress. This only tracks turn-taking state — deciding what to *do*
about an interruption belongs to the deliberation engine (a later phase).
"""

from __future__ import annotations

from enum import Enum


class TurnState(Enum):
    IDLE = "idle"
    CALLER_SPEAKING = "caller_speaking"
    AGENT_SPEAKING = "agent_speaking"
    INTERRUPTED = "interrupted"
    WAITING = "waiting"


class TurnStateMachine:
    def __init__(self) -> None:
        self._state = TurnState.IDLE

    @property
    def state(self) -> TurnState:
        return self._state

    def caller_starts_speaking(self) -> bool:
        """Mark the caller as speaking.

        Returns True if this is a barge-in (the agent was speaking), in
        which case the state becomes INTERRUPTED rather than
        CALLER_SPEAKING directly — the caller has the floor, but the
        engine still needs to stop agent playback before normal
        turn-taking resumes.
        """
        is_barge_in = self._state == TurnState.AGENT_SPEAKING
        self._state = TurnState.INTERRUPTED if is_barge_in else TurnState.CALLER_SPEAKING
        return is_barge_in

    def acknowledge_interruption(self) -> None:
        """Call once agent playback has actually been stopped.

        Moves from INTERRUPTED to CALLER_SPEAKING — the caller now holds
        the floor under normal turn-taking, the interruption itself is
        over.
        """
        if self._state == TurnState.INTERRUPTED:
            self._state = TurnState.CALLER_SPEAKING

    def caller_stops_speaking(self) -> None:
        self._state = TurnState.WAITING

    def agent_starts_speaking(self) -> None:
        self._state = TurnState.AGENT_SPEAKING

    def agent_stops_speaking(self) -> None:
        self._state = TurnState.IDLE

    def reset(self) -> None:
        self._state = TurnState.IDLE
