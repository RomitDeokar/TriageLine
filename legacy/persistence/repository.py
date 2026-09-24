"""Repository interfaces + SQLite implementations for calls, deliberation
records, and commit actions.

These are the only modules that translate between core dataclasses
(DeliberationRecord, CommitRecord) and SQL rows. core/deliberation and
core/commit never import this module -- they depend only on the small
Protocol each defines (DeliberationRepositoryProtocol,
CommitRepositoryProtocol), satisfied here, so core stays testable without
a database (docs/ARCHITECTURE.md section 1, goal 4).

Historical deliberation rows are never overwritten except for the one
`superseded_by` pointer -- enforced at the SQL layer itself via
`ON CONFLICT ... DO UPDATE SET superseded_by = excluded.superseded_by`, so
even a bug elsewhere can't silently rewrite an old record's reasoning
content (continuation spec section 14, NFR-5).
"""

from __future__ import annotations

import json
from typing import Optional

from core.commit.state_machine import CommitRecord, CommitState
from core.deliberation.record import ConstraintResult, DeliberationRecord, Option
from persistence.db import Database


class CallRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def ensure_call(self, call_id: str, created_at_ms: int = 0) -> None:
        with self._db.connection:
            self._db.connection.execute(
                "INSERT OR IGNORE INTO calls (call_id, created_at_ms) VALUES (?, ?)",
                (call_id, created_at_ms),
            )

    def list_calls(self) -> list[str]:
        rows = self._db.connection.execute(
            "SELECT call_id FROM calls ORDER BY created_at_ms"
        ).fetchall()
        return [row["call_id"] for row in rows]


class DeliberationRepository:
    """Implements DeliberationRepositoryProtocol (core/deliberation/engine.py)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save_deliberation(self, record: DeliberationRecord) -> None:
        with self._db.connection:
            self._db.connection.execute(
                """
                INSERT INTO deliberation_records (
                    decision_id, call_id, intent, known_facts_json, uncertainties_json,
                    options_json, chosen_option, rationale, confidence,
                    constraint_results_json, resolved, fallback_action,
                    supersedes, superseded_by, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    superseded_by = excluded.superseded_by
                """,
                (
                    record.decision_id,
                    record.call_id,
                    record.intent,
                    json.dumps(record.known_facts),
                    json.dumps(record.uncertainties),
                    json.dumps([o.to_dict() for o in record.options_considered]),
                    record.chosen_option,
                    record.rationale,
                    record.confidence,
                    json.dumps([c.to_dict() for c in record.constraint_results]),
                    int(record.resolved),
                    record.fallback_action,
                    record.supersedes,
                    record.superseded_by,
                    0,
                ),
            )

    def get_deliberation(self, decision_id: str) -> Optional[DeliberationRecord]:
        row = self._db.connection.execute(
            "SELECT * FROM deliberation_records WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def list_for_call(self, call_id: str) -> list[DeliberationRecord]:
        rows = self._db.connection.execute(
            "SELECT * FROM deliberation_records WHERE call_id = ? ORDER BY created_at_ms",
            (call_id,),
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row) -> DeliberationRecord:
        options = [Option(**o) for o in json.loads(row["options_json"])]
        constraints = [ConstraintResult(**c) for c in json.loads(row["constraint_results_json"])]
        return DeliberationRecord(
            decision_id=row["decision_id"],
            call_id=row["call_id"],
            intent=row["intent"],
            known_facts=json.loads(row["known_facts_json"]),
            uncertainties=json.loads(row["uncertainties_json"]),
            options_considered=options,
            chosen_option=row["chosen_option"],
            rationale=row["rationale"],
            confidence=row["confidence"],
            constraint_results=constraints,
            resolved=bool(row["resolved"]),
            fallback_action=row["fallback_action"],
            supersedes=row["supersedes"],
            superseded_by=row["superseded_by"],
        )


class ActionRepository:
    """Implements CommitRepositoryProtocol (core/commit/state_machine.py)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save_action(self, record: CommitRecord) -> None:
        with self._db.connection:
            self._db.connection.execute(
                """
                INSERT INTO commit_actions (
                    action_id, call_id, decision_id, action_type, action_payload_json,
                    current_state, created_at_ms, updated_at_ms,
                    confirmed_by, confirmed_at_ms, aborted_reason, aborted_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(action_id) DO UPDATE SET
                    current_state = excluded.current_state,
                    updated_at_ms = excluded.updated_at_ms,
                    confirmed_by = excluded.confirmed_by,
                    confirmed_at_ms = excluded.confirmed_at_ms,
                    aborted_reason = excluded.aborted_reason,
                    aborted_at_ms = excluded.aborted_at_ms
                """,
                (
                    record.action_id,
                    record.call_id,
                    record.decision_id,
                    record.action_type,
                    json.dumps(record.action_payload),
                    record.current_state.value,
                    record.created_at_ms,
                    record.updated_at_ms,
                    record.confirmed_by,
                    record.confirmed_at_ms,
                    record.aborted_reason,
                    record.aborted_at_ms,
                ),
            )

    def get_action(self, action_id: str) -> Optional[CommitRecord]:
        row = self._db.connection.execute(
            "SELECT * FROM commit_actions WHERE action_id = ?", (action_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def list_for_call(self, call_id: str) -> list[CommitRecord]:
        rows = self._db.connection.execute(
            "SELECT * FROM commit_actions WHERE call_id = ? ORDER BY created_at_ms",
            (call_id,),
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row) -> CommitRecord:
        return CommitRecord(
            action_id=row["action_id"],
            call_id=row["call_id"],
            decision_id=row["decision_id"],
            action_type=row["action_type"],
            action_payload=json.loads(row["action_payload_json"]),
            current_state=CommitState(row["current_state"]),
            created_at_ms=row["created_at_ms"],
            updated_at_ms=row["updated_at_ms"],
            confirmed_by=row["confirmed_by"],
            confirmed_at_ms=row["confirmed_at_ms"],
            aborted_reason=row["aborted_reason"],
            aborted_at_ms=row["aborted_at_ms"],
        )
