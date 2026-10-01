#!/bin/bash
# Resilient FDB-v3 live runner (LOCAL RECOVERY ONLY — the canonical submission path is run_fdb_v3.sh).
#
# Why it exists: on this Windows dev machine an HP "SystemOptimizer" process randomly kills the
# agent worker mid-run. This script runs in passes: each pass deletes invalid results, starts a
# FRESH worker, runs the official inference (skipping examples that already have valid results),
# and loops until 100/100 results are `status == completed` with a non-empty transcript, then runs
# the official evaluators. The SCORED CONFIG IS PINNED here (same as run_fdb_v3.sh stage 2) so the
# numbers are comparable: rules-only benchmark agent, rules-first LLM mode, planner OFF.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
# ---- pinned scored configuration (identical to run_fdb_v3.sh) ----
export TRIAGELINE_MODE=benchmark TRIAGELINE_BENCHMARK_POLICY=1
export TRIAGELINE_LLM_PLANNER=0 TRIAGELINE_LLM_MODE=fallback
export TRIAGELINE_AGENT_NAME=""
export TRIAGELINE_STT_PROVIDER=deepgram TRIAGELINE_TTS_PROVIDER=deepgram
V3="$ROOT/livekit_agent/.fdb_v3_repo/v3"
PY="$ROOT/.venv-fdb/Scripts/python.exe"
DATA="$V3/fdb_v3_data_released"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/results/live_$STAMP"
mkdir -p "$OUT"
LOG="$OUT/run.log"

valid_count() {
  "$PY" - "$DATA" <<'PYEOF'
import json, pathlib, sys
base = pathlib.Path(sys.argv[1]); n = 0
for d in base.iterdir():
    r = d / "result_triageline.json"
    if r.exists():
        try:
            j = json.loads(r.read_text(encoding="utf-8"))
            if j.get("status") == "completed" and (j.get("transcript") or "").strip():
                n += 1
        except Exception:
            pass
print(n)
PYEOF
}

purge_invalid() {
  "$PY" - "$DATA" <<'PYEOF'
import json, pathlib, sys
base = pathlib.Path(sys.argv[1])
for d in base.iterdir():
    r = d / "result_triageline.json"
    if r.exists():
        try:
            j = json.loads(r.read_text(encoding="utf-8"))
            ok = j.get("status") == "completed" and (j.get("transcript") or "").strip()
            if not ok:
                r.unlink()
                for extra in ("output_triageline.wav", "result_triageline_text.json"):
                    (d / extra).unlink(missing_ok=True)
        except Exception:
            pass
PYEOF
}

"$PY" - "$OUT/run_config.json" "$DATA" <<'PYEOF'
import json, os, subprocess, sys, time
out, data = sys.argv[1], sys.argv[2]
def git(cmd):
    try:
        return subprocess.check_output(["git"] + cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""
cfg = {
    "runner": "resilient_fdb_run.sh (local recovery; canonical path is run_fdb_v3.sh)",
    "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "pinned_config": {k: os.environ.get(k) for k in (
        "TRIAGELINE_MODE", "TRIAGELINE_BENCHMARK_POLICY", "TRIAGELINE_LLM_PLANNER",
        "TRIAGELINE_LLM_MODE", "TRIAGELINE_AGENT_NAME", "TRIAGELINE_STT_PROVIDER",
        "TRIAGELINE_TTS_PROVIDER")},
    "fdb_v3_commit": git(["-C", os.path.dirname(os.path.dirname(os.path.dirname(data))), "rev-parse", "HEAD"]),
    "repo_commit": git(["-C", os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(data)))), "rev-parse", "HEAD"]),
    "judge": "off (no OPENAI_API_KEY; organisers re-run with their pinned judge)",
}
json.dump(cfg, open(out, "w"), indent=2)
print("run_config.json written")
PYEOF

echo "=== resilient loop start $(date -u +%FT%TZ) | out=$OUT ===" | tee -a "$LOG"
for pass in $(seq 1 40); do
  purge_invalid
  V=$(valid_count)
  echo "[pass $pass] valid=$V/100 $(date -u +%T)" | tee -a "$LOG"
  if [ "$V" -ge 100 ]; then break; fi
  # kill only the worker THIS loop started (pid file) — never the demo workers
  if [ -f "$OUT/worker.pid" ]; then
    WID=$(tr -d '
' < "$OUT/worker.pid")
    [ -n "$WID" ] && taskkill //PID "$WID" //F >/dev/null 2>&1
    rm -f "$OUT/worker.pid"
  fi
  sleep 2
  ( cd "$ROOT/livekit_agent" && "$PY" cascaded_agent.py dev ) >"$OUT/worker_$pass.log" 2>&1 &
  sleep 3
  WID=$(powershell -NoProfile -Command 'Get-CimInstance Win32_Process | Where-Object { $_.Name -match "python" -and $_.CommandLine -match "cascaded_agent" } | Sort-Object CreationDate -Descending | Select-Object -First 1 -ExpandProperty ProcessId' 2>/dev/null | tr -d '\r\n')
  echo "$WID" > "$OUT/worker.pid"
  # liveness: is the worker WE started still alive? (pid-scoped, so demo workers never matter)
  ALIVE=0
  for _ in $(seq 1 15); do
    ALIVE=$(powershell -NoProfile -Command "if ($WID -and (Get-Process -Id $WID -ErrorAction SilentlyContinue)) { 1 } else { 0 }" 2>/dev/null | tr -d '\r\n')
    [ "${ALIVE:-0}" -ge 1 ] && break
    sleep 2
  done
  if [ "${ALIVE:-0}" -lt 1 ]; then
    echo "[pass $pass] worker failed to start; retrying" | tee -a "$LOG"
    continue
  fi
  ( cd "$V3" && "$PY" run_tool_benchmark_all_released.py --provider triageline ) >>"$LOG" 2>&1
  echo "[pass $pass] inference done $(date -u +%T)" | tee -a "$LOG"
done

V=$(valid_count)
echo "=== final valid=$V/100 $(date -u +%T) ===" | tee -a "$LOG"
if [ "$V" -lt 100 ]; then
  echo "BLOCKER: only $V/100 valid results after 40 passes; refusing to publish a partial score" | tee -a "$LOG"
  exit 1
fi
( cd "$V3" && "$PY" evaluate_tool_calls.py --benchmark benchmark_data_v2.json --results-dir "$DATA" \
    --provider triageline --output "$OUT/evaluation_report.json" ) >>"$LOG" 2>&1 \
  && echo "tool_calls eval OK" | tee -a "$LOG" || { echo "BLOCKER: evaluate_tool_calls failed" | tee -a "$LOG"; exit 1; }
( cd "$V3" && "$PY" evaluate_pass_rate.py --benchmark benchmark_data_v2.json --results-dir "$DATA" \
    --provider triageline --output "$OUT/pass_rate_report.json" ) >>"$LOG" 2>&1 \
  && echo "pass_rate eval OK" | tee -a "$LOG" || { echo "BLOCKER: evaluate_pass_rate failed" | tee -a "$LOG"; exit 1; }
"$PY" -m pip freeze > "$OUT/pip_freeze.txt" 2>/dev/null || true
cp /c/tmp/agent_tool_calls.log "$OUT/agent_tool_calls.log" 2>/dev/null || true
cp /c/tmp/agent_heartbeat.log "$OUT/agent_heartbeat.log" 2>/dev/null || true
echo "=== done $(date -u +%FT%TZ) ===" | tee -a "$LOG"
