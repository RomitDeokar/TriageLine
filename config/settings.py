"""Application configuration foundation.

A single, simple Settings object with sensible defaults. Values are read
from environment variables where present, falling back to defaults below.
No secrets or API keys are stored here — later phases will add
provider-credential handling separately (env-driven, never hardcoded).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value is not None else default


@dataclass
class Settings:
    """Central, environment-driven configuration.

    Fields are intentionally minimal for Phase 1. Later phases will extend
    this with provider settings, event bus options, etc.
    """

    environment: str = _env_str("TRIAGE_ENV", "development")
    strategy: str = _env_str("TRIAGE_STRATEGY", "deliberative")  # or "naive"
    database_path: str = _env_str("TRIAGE_DB_PATH", "triage_line.db")
    barge_in_target_latency_ms: int = _env_int("TRIAGE_BARGE_IN_MS", 150)
    provider_mode: str = _env_str("TRIAGE_PROVIDER_MODE", "mock")  # or "live"
    service_radius_miles: int = _env_int("TRIAGE_SERVICE_RADIUS_MILES", 25)


settings = Settings()
