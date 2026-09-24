"""SQLite schema + connection management.

A single, minimal `Database` wrapper around `sqlite3` -- the only file in
this project that imports `sqlite3` directly. `persistence/repository.py`
is the only other module that touches a `Database`'s connection; core
business logic (core/deliberation, core/commit) never imports this module
or sqlite3 at all, only the small repository interfaces those modules
define as Protocols and receive via dependency injection.

No network calls, no external database service -- SQLite is always local,
per the continuation spec's offline-testing requirement.
"""

from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS deliberation_records (
    decision_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    intent TEXT NOT NULL,
    known_facts_json TEXT NOT NULL,
    uncertainties_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    chosen_option TEXT,
    rationale TEXT NOT NULL,
    confidence REAL NOT NULL,
    constraint_results_json TEXT NOT NULL,
    resolved INTEGER NOT NULL,
    fallback_action TEXT,
    supersedes TEXT,
    superseded_by TEXT,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS commit_actions (
    action_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    action_payload_json TEXT NOT NULL,
    current_state TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    confirmed_by TEXT,
    confirmed_at_ms INTEGER,
    aborted_reason TEXT,
    aborted_at_ms INTEGER
);
"""


class Database:
    """Thin wrapper: one SQLite connection plus schema initialization.

    Defaults to an in-memory database so callers who don't care about
    on-disk persistence (e.g. a quick script) get a working, isolated
    database with zero configuration. Tests that need to verify
    restart/reload behavior pass a real temp-file path instead (an
    in-memory database only exists for the lifetime of one connection, so
    it can't be reopened by a second `Database` instance).
    """

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def initialize_schema(self) -> None:
        with self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()
