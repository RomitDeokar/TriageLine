"""Commit strategy seam.

docs/ARCHITECTURE.md names this file for a future "naive vs deliberative
commit strategy" comparison (naive commits immediately with no
confirmation-safety, for a head-to-head evaluation against the
deliberative agent). That comparison -- and any strategy that skips
confirmation-gating -- is explicitly out of scope for Phase 5 (continuation
spec section 21: "Naive-vs-Deliberative comparison" is future work, and
section 4's "NO ACTION MAY BE FINALIZED WITHOUT CONFIRMATION" is an
absolute rule for this phase, not something a strategy may opt out of).

So this module ships exactly one strategy -- the deterministic,
deliberative default -- plus the interface a future phase can implement
against without touching core/commit/state_machine.py. The one thing a
CommitStrategy controls is whether `propose()` immediately requests
confirmation (moves PROPOSED -> PENDING_CONFIRMATION on its own) or leaves
that as a separate explicit step; it never controls -- and cannot skip --
the requirement that `confirm()` is the only path to FINALIZED.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class CommitStrategy(ABC):
    name: str = "commit_strategy"

    @abstractmethod
    def auto_request_confirmation(self) -> bool:
        """Whether CommitStateMachine.propose() should immediately advance
        PROPOSED -> PENDING_CONFIRMATION on its own behalf.

        This never shortcuts the requirement that only an explicit
        `confirm()` call reaches FINALIZED (see
        core/commit/state_machine.py's docstring) -- it only controls how
        quickly a proposal starts waiting for that confirmation.
        """


class DeliberativeCommitStrategy(CommitStrategy):
    """The only strategy shipped in Phase 5: request confirmation immediately.

    Once an action is proposed, the caller-facing confirmation prompt
    ("Dispatching a tow truck to your location -- is that okay?") is
    issued right away; explicit confirm()/abort() is still required to
    reach a terminal state (see docs/UI_SPEC.md's dispatch stepper, which
    always shows PENDING CONFIRM as its own visible step).
    """

    name = "deliberative"

    def auto_request_confirmation(self) -> bool:
        return True
