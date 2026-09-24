"""Manual test runner for environments without pytest/network access.

Discovers every tests/test_*.py module, imports it, and calls every
top-level `test_*` function, reporting pass/fail counts. This mirrors
what `pytest -q` would do for our purposes (plain assert-based tests,
no fixtures/parametrize), per docs/BUILD_PROMPT.md's testing-environment
fallback instructions.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_DIR = ROOT / "tests"


def discover_test_modules() -> list[str]:
    names = []
    for path in sorted(TEST_DIR.glob("test_*.py")):
        names.append(f"tests.{path.stem}")
    return names


def run() -> int:
    total = 0
    failed = 0
    failures: list[tuple[str, str]] = []
    skipped_modules: list[tuple[str, str]] = []

    for module_name in discover_test_modules():
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            # An optional dependency (e.g. fastapi/httpx for tests/test_api.py)
            # isn't installed in this environment. This is reported plainly
            # as a skipped module, never silently treated as passing, and
            # never allowed to crash the rest of the suite.
            skipped_modules.append((module_name, str(exc)))
            print(f"SKIP  {module_name} (missing dependency: {exc})")
            continue

        test_names = sorted(n for n in dir(module) if n.startswith("test_"))
        for name in test_names:
            fn = getattr(module, name)
            if not callable(fn):
                continue
            total += 1
            full_name = f"{module_name}.{name}"
            try:
                fn()
            except Exception:  # noqa: BLE001 - want to report every failure
                failed += 1
                failures.append((full_name, traceback.format_exc()))
            else:
                print(f"PASS  {full_name}")

    print()
    print(f"Ran {total} tests, {total - failed} passed, {failed} failed.")
    if skipped_modules:
        print(f"{len(skipped_modules)} module(s) skipped (missing optional dependency):")
        for name, reason in skipped_modules:
            print(f"  - {name}: {reason}")
    if failures:
        print()
        print("=== FAILURES ===")
        for name, tb in failures:
            print(f"--- {name} ---")
            print(tb)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
