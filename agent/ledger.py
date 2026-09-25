"""Operation ledger for state-modifying tool calls (audit R03, R04, R05, R16).

Cancelling a local task does not prove that a remote side effect was rolled back. Every state-modifying call
therefore gets a durable, in-session operation record whose lifecycle is explicit:

    pending ─┬─▶ committed        tool returned success with completion evidence
             ├─▶ rejected         tool returned a definitive error (invalid_args, not_found, ...)
             ├─▶ unknown          transport timeout, malformed success, provider exception,
             │                    or a cancel the provider cannot confirm
             └─▶ cancel_requested we sent cancel_tool; outcome not yet reconciled
    cancel_requested ─┬─▶ committed   late success arrived → disclosed to the user (R03)
                      ├─▶ cancelled   provider confirms cancel is authoritative, or a late error arrived
                      └─▶ unknown     provider cannot confirm (live / real providers)
    committed ─▶ reversed             a later confirmed cancellation undid it (R05)

Rules this module enforces (and tests pin):
  * `unknown` never silently becomes retryable just because the user repeats themselves (R04);
    only an explicit, confirmed user decision can supersede it, and the original record is kept.
  * a `committed` op whose business effect was reversed allows a fresh, newly authorised attempt (R05).
  * "success" without completion evidence is `unknown`, never `committed` (R16).
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional

# statuses that block an identical new attempt
BLOCKING = {"pending", "committed", "unknown", "cancel_requested"}
# statuses after which a fresh attempt is allowed
RETRYABLE = {"rejected", "cancelled", "reversed", "superseded"}

# definitive errors: the provider tells us nothing was committed
DEFINITIVE_ERRORS = {"invalid_args", "not_found", "unknown_tool", "unavailable", "duplicate_booking",
                     "validation_error", "forbidden", "unauthorized"}
# ambiguous errors: something may have committed
AMBIGUOUS_ERRORS = {"timeout", "error", "provider_exception", "connection_error", "malformed_result"}

# completion evidence required from known state-modifying tools (R16)
EVIDENCE = {
    "book_flight": ("booking_id", "booking_ref", "confirmation"),
    "cancel_booking": ("cancelled", "cancelled_booking_id", "booking_id"),
    "create_support_ticket": ("ticket_id",),
    # FDB-v3 mock tools
    "update_identity_doc": ("updated_doc",),
    "modify_autopay": ("autopay_enabled",),
    "add_to_cart": ("product_id", "cart_total"),
    "update_search_filter": ("filter_updated",),
}


def has_evidence(api: str, result: Dict[str, Any]) -> bool:
    """True if a nominal success carries proof of completion."""
    if not isinstance(result, dict):
        return False
    keys = EVIDENCE.get(api)
    if keys:
        return any(result.get(k) not in (None, "", False) for k in keys)
    # unseen state-modifying tool: accept an id/reference/confirmation-like field, or any non-empty payload
    for k, v in result.items():
        if k in ("status", "detail", "message"):
            continue
        if v in (None, "", [], {}):
            continue
        return True
    return False


class OperationLedger:
    def __init__(self):
        self.ops: Dict[str, Dict[str, Any]] = {}          # idempotency key -> latest record for that key
        self.by_call: Dict[str, Dict[str, Any]] = {}      # call_id -> record
        self.history: List[Dict[str, Any]] = []           # every record ever created (never deleted)
        self._n = itertools.count(1)

    # ------------------------------------------------------------------ lookup
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.ops.get(key)

    def for_call(self, cid: str) -> Optional[Dict[str, Any]]:
        return self.by_call.get(cid)

    def blocking(self, key: str) -> Optional[Dict[str, Any]]:
        op = self.ops.get(key)
        return op if op and op["status"] in BLOCKING else None

    def unresolved(self, api: Optional[str] = None) -> List[Dict[str, Any]]:
        """Ops whose real-world outcome is still open (block conflicting replacement commits)."""
        return [o for o in self.history if o["status"] in ("unknown", "cancel_requested")
                and (api is None or o["api"] == api)]

    # ------------------------------------------------------------------ transitions
    def open(self, key: str, api: str, args: Dict[str, Any], cid: str, ctx: Dict[str, Any],
             supersedes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        rec = {"op_id": f"op{next(self._n)}", "key": key, "api": api, "args": dict(args), "call_id": cid,
               "status": "pending", "result": None, "ctx": dict(ctx), "events": ["pending"],
               "idempotency_key": None, "supersedes": supersedes["op_id"] if supersedes else None}
        rec["idempotency_key"] = rec["op_id"] + ":" + key[:80]
        if supersedes is not None:
            supersedes["superseded_by"] = rec["op_id"]
            self._move(supersedes, "superseded")
        self.ops[key] = rec
        self.by_call[cid] = rec
        self.history.append(rec)
        return rec

    def _move(self, rec: Dict[str, Any], status: str, **kw):
        rec["status"] = status
        rec["events"].append(status)
        rec.update(kw)

    def commit(self, rec, result):
        self._move(rec, "committed", result=result)

    def reject(self, rec, error):
        self._move(rec, "rejected", error=error)

    def unknown(self, rec, why):
        self._move(rec, "unknown", error=why)

    def cancel_requested(self, rec):
        if rec["status"] == "pending":
            self._move(rec, "cancel_requested")

    def cancel_confirmed(self, rec):
        if rec["status"] in ("pending", "cancel_requested"):
            self._move(rec, "cancelled")

    def reverse(self, rec, by: str):
        if rec["status"] == "committed":
            self._move(rec, "reversed", reversed_by=by)

    def find_committed(self, api: str, field: str, value: Any) -> Optional[Dict[str, Any]]:
        for rec in reversed(self.history):
            if rec["api"] == api and rec["status"] == "committed" and \
                    str((rec.get("result") or {}).get(field)) == str(value):
                return rec
        return None

    def summary(self) -> List[Dict[str, Any]]:
        return [{k: r[k] for k in ("op_id", "api", "status", "call_id", "supersedes") if k in r}
                for r in self.history]
