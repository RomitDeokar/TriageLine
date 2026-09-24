"""Deterministic, offline mock LLM provider.

Uses simple, rule-based logic over the conversation context so tests get
predictable output. This is test infrastructure standing in for a real
model — it does not perform the project's actual deliberation or
self-critique, which is implemented in the deliberation engine (a later
phase). It only produces the kind of structured response that layer will
consume.
"""

from __future__ import annotations

from providers.interfaces import (
    ActionProposal,
    ConversationTurn,
    LLMProvider,
    LLMResponse,
)

_LOCATION_HINTS = ("highway", "hwy", "street", "st.", "mile", "road", "avenue")


def _caller_text(context: list[ConversationTurn]) -> str:
    return " ".join(turn.text.lower() for turn in context if turn.speaker == "caller")


def _has_location(text: str) -> bool:
    return any(hint in text for hint in _LOCATION_HINTS) or any(c.isdigit() for c in text)


class MockLLM(LLMProvider):
    """Rule-based scripted responder, evaluated fresh on each call.

    Rule order matters and is what makes contradiction-handling
    deterministic: "fire" always wins over an earlier "flat tire" /
    "broke down" mention, since the full context is re-evaluated each
    call rather than remembering a prior decision.
    """

    async def respond(self, context: list[ConversationTurn]) -> LLMResponse:
        text = _caller_text(context)

        if "fire" in text:
            return LLMResponse(
                text="Escalating to emergency services now.",
                intent="emergency",
                action_proposal=ActionProposal(
                    action_type="escalate_emergency", details={"reason": "fire"}
                ),
                needs_more_info=False,
            )

        if "flat tire" in text or "broke down" in text or "breakdown" in text:
            if not _has_location(text):
                return LLMResponse(
                    text="Got it, what's your location?",
                    intent="breakdown",
                    action_proposal=None,
                    needs_more_info=True,
                )
            return LLMResponse(
                text="Dispatching a tow truck to your location.",
                intent="breakdown",
                action_proposal=ActionProposal(
                    action_type="dispatch_tow", details={"reason": "breakdown"}
                ),
                needs_more_info=False,
            )

        return LLMResponse(
            text="Okay, tell me more about what's going on.",
            intent=None,
            action_proposal=None,
            needs_more_info=False,
        )
