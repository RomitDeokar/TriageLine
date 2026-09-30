#!/bin/bash
# Resilient FDB-v3 live runner: defeats random process kills (HP SystemOptimizer etc.)
# by running in passes: each pass deletes silent results, starts a FRESH worker,
# runs the official inference (skipping examples that already have good results),
# and loops until 100/100 results carry a non-empty transcript. Then evaluates.
set -u
cd "$(dirname "$0")"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 TRIAGELINE_AGENT_NAME=""
V3=livekit_agent/.fdb_v3_repo/v3
PY="$(pwd)/.venv-fdb/Scripts/python.exe"
DATA=$V3/fdb_v3_data_released
LOG=results/resilient_loop.log

valid_count() {
  "$PY" - "$DATA" <<'PYEOF'
import json, pathlib, sys
base = pathlib.Path(sys.argv[1])
n = 0
for d in base.iterdir():
    r = d / "result_triageline.json"
    if r.exists():
        try:
            j = json.loads(r.read_text(encoding="utf-8"))
            if (j.get("transcript") or "").strip() or j.get("asr_chunks"):
                n += 1
        except Exception:
            pass
print(n)
PYEOF
}

purge_silent() {
  "$PY" - "$DATA" <<'PYEOF'
import json, pathlib, sys
base = pathlib.Path(sys.argv[1])
for d in base.iterdir():
    r = d / "result_triageline.json"
    if r.exists():
        try:
            j = json.loads(r.read_text(encoding="utf-8"))
            if not ((j.get("transcript") or "").strip() or j.get("asr_chunks")):
                r.unlink()
                wav = d / "output_triageline.wav"
                if wav.exists():
                    wav.unlink()
        except Exception:
            pass
PYEOF
}

echo "=== resilient loop start $(date -u +%FT%TZ) ===" | tee -a "$LOG"
for pass in $(seq 1 30); do
  purge_silent
  V=$(valid_count)
  echo "[pass $pass] valid=$V/100 $(date -u +%T)" | tee -a "$LOG"
  if [ "$V" -ge 100 ]; then break; fi
  # fresh worker every pass (kills zombies, resets registrations)
  powershell -NoProfile -Command 'Get-CimInstance Win32_Process | Where-Object { $_.Name -match "python" -and $_.CommandLine -match "cascaded_agent" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }' 2>/dev/null
  sleep 2
  ( cd livekit_agent && exec ../.venv-fdb/Scripts/python.exe cascaded_agent.py dev ) >results/loop_worker.log 2>&1 &
  WORKER=$!
  sleep 20
  if ! kill -0 "$WORKER" 2>/dev/null; then
    echo "[pass $pass] worker died at startup; retrying" | tee -a "$LOG"
    continue
  fi
  ( cd "$V3" && "$PY" run_tool_benchmark_all_released.py --provider triageline ) >>"$LOG" 2>&1
  echo "[pass $pass] inference done $(date -u +%T)" | tee -a "$LOG"
done

V=$(valid_count)
echo "=== final valid=$V/100; running evaluators ===" | tee -a "$LOG"
( cd "$V3" && "$PY" evaluate_tool_calls.py --benchmark benchmark_data_v2.json --results-dir "$DATA" \
    --provider triageline --output ../../results/20260930_live_run/evaluation_report.json ) >>"$LOG" 2>&1 \
  && echo "tool_calls eval OK" | tee -a "$LOG"
( cd "$V3" && "$PY" evaluate_pass_rate.py --benchmark benchmark_data_v2.json --results-dir "$DATA" \
    --provider triageline --output ../../results/20260930_live_run/pass_rate_report.json ) >>"$LOG" 2>&1 \
  && echo "pass_rate eval OK" | tee -a "$LOG"
echo "=== done $(date -u +%FT%TZ) ===" | tee -a "$LOG"
