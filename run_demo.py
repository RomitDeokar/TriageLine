"""Triage Line demo entry point.

Phase 1 status: this is a placeholder. The voice pipeline, deliberation
engine, commit state machine, and UI are not implemented yet — this
script only confirms the project scaffolding is importable and runnable.
"""

from config.settings import settings


def main() -> None:
    print("Triage Line — Phase 1: scaffolding only.")
    print("The full voice/deliberation/dispatch demo is not implemented yet.")
    print(f"environment: {settings.environment}")
    print(f"strategy: {settings.strategy}")
    print(f"database_path: {settings.database_path}")
    print(f"barge_in_target_latency_ms: {settings.barge_in_target_latency_ms}")
    print(f"provider_mode: {settings.provider_mode}")


if __name__ == "__main__":
    main()
