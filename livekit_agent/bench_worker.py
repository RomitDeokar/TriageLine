"""Benchmark worker entry point (identical to cascaded_agent.py, distinct process name).

Why it exists: the local recovery runner (resilient_fdb_run.sh) restarts the benchmark worker by
killing processes whose command line matches its own entry point. Running the benchmark through this
wrapper keeps that cleanup from ever touching the demo workers (assistant / triage), which run
`cascaded_agent.py` directly.

    python livekit_agent/bench_worker.py dev     # local benchmark worker (automatic dispatch)
    python livekit_agent/bench_worker.py start   # production form used by run_fdb_v3.sh
"""
from __future__ import annotations

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

os.environ.setdefault("TRIAGELINE_AGENT_NAME", "")     # automatic dispatch for the official runner
os.environ.setdefault("TRIAGELINE_MODE", "benchmark")

if __name__ == "__main__":
    runpy.run_path(os.path.join(HERE, "cascaded_agent.py"), run_name="__main__")
