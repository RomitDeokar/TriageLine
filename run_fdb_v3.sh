#!/usr/bin/env bash
# run_fdb_v3.sh — single reproduction command for the official FDB-v3 v3
# evaluation against the TriageLine LiveKit agent.
#
# Stages: install deps -> check env/credentials -> fetch official FDB-v3
# benchmark material -> launch LiveKit agent -> run official FDB-v3 eval
# (LLM judge enabled) -> save results/ -> print summary.
#
# Each stage is a separate function so a partial environment fails at the
# FIRST missing thing with an actionable message, instead of a confusing
# failure three stages later. This script does NOT fabricate results: if a
# stage cannot complete, it stops, writes what it learned to
# results/raw/environment_check.log, and exits non-zero.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LK_DIR="$ROOT_DIR/livekit_agent"
RESULTS_DIR="$ROOT_DIR/results"
RAW_DIR="$RESULTS_DIR/raw"
LOG="$RAW_DIR/environment_check.log"
FDB_DATA_DIR="$LK_DIR/fdb_v3_data_released"
FDB_REPO_DIR="$LK_DIR/.fdb_v3_repo"

mkdir -p "$RAW_DIR"
: > "$LOG"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

fail() {
    log "BLOCKER: $*"
    log "Reproduction cannot proceed past this stage in the current environment."
    exit 1
}

stage_1_install_deps() {
    log "=== Stage 1/6: install dependencies ==="
    if ! command -v python3 >/dev/null; then fail "python3 not found"; fi
    python3 -c "import sys; assert sys.version_info >= (3,10)" \
        || log "WARNING: FDB-v3 README targets py3.10; livekit_agent/SETUP.md was authored/tested on 3.12"
    if python3 -c "import livekit" >/dev/null 2>&1; then
        log "livekit already importable, skipping install"
    else
        log "attempting: pip install 'livekit-agents[openai]~=1.3' livekit-plugins-silero livekit livekit-api python-dotenv numpy"
        if pip install "livekit-agents[openai]~=1.3" livekit-plugins-silero \
                livekit livekit-api python-dotenv numpy --break-system-packages \
                >> "$LOG" 2>&1; then
            log "livekit-agents installed"
        else
            fail "pip install failed (no network egress to PyPI in this environment). See $LOG. \
Fix: run this script somewhere with outbound network access, or pre-populate a local wheel cache."
        fi
    fi
}

stage_2_check_env() {
    log "=== Stage 2/6: configure/check environment ==="
    local missing=()
    for v in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET OPENAI_API_KEY; do
        if [ -z "${!v:-}" ]; then missing+=("$v"); fi
    done
    if [ ${#missing[@]} -gt 0 ]; then
        fail "missing required environment variable(s): ${missing[*]}. \
Fix: create a LiveKit Cloud project + OpenAI API key (see livekit_agent/SETUP.md \
'Accounts / keys'), then 'cp livekit_agent/.env.example livekit_agent/.env.local' \
and fill in values, or export them directly before re-running this script."
    fi
    log "required credentials present"
}

stage_3_fetch_fdb_data() {
    log "=== Stage 3/6: fetch official FDB-v3 benchmark material ==="
    log "Required: github.com/DanielLin94144/Full-Duplex-Bench (v3/) + its data \
release (100 human recordings, 12 speakers, 79 scenarios, official \
annotations/config) — per livekit_agent/SETUP.md, the audio bundle is \
distributed via Google Drive, not the git repo."
    if [ -d "$FDB_DATA_DIR" ] && [ -n "$(ls -A "$FDB_DATA_DIR" 2>/dev/null)" ]; then
        log "found existing $FDB_DATA_DIR, using it"
        return 0
    fi
    if git clone --depth 1 https://github.com/DanielLin94144/Full-Duplex-Bench "$FDB_REPO_DIR" >> "$LOG" 2>&1; then
        log "cloned FDB-v3 repo to $FDB_REPO_DIR"
    else
        fail "could not clone github.com/DanielLin94144/Full-Duplex-Bench (no network egress \
in this environment). Fix: run with network access, or manually place the official \
v3/ tree + its Google-Drive data release (100 recordings/12 speakers/79 scenarios/ \
official annotations) at $FDB_DATA_DIR before re-running."
    fi
    fail "FDB-v3 repo cloned but the Google-Drive audio/annotation bundle still \
needs to be downloaded and placed at $FDB_DATA_DIR — that step needs a Drive \
download this script cannot perform unattended. Fix: download it manually per \
the FDB-v3 v3 README, then re-run."
}

stage_4_launch_agent() {
    log "=== Stage 4/6: launch LiveKit agent ==="
    ( cd "$LK_DIR" && python3 cascaded_agent.py start >> "$LOG" 2>&1 & echo $! > "$RAW_DIR/agent.pid" )
    sleep 2
    if ! kill -0 "$(cat "$RAW_DIR/agent.pid")" 2>/dev/null; then
        fail "cascaded_agent.py exited immediately on start — see $LOG"
    fi
    log "agent launched, pid $(cat "$RAW_DIR/agent.pid")"
}

stage_5_run_fdb_eval() {
    log "=== Stage 5/6: run official FDB-v3 evaluation (LLM judge enabled) ==="
    fail "official FDB-v3 v3 evaluation entrypoint lives in the repo cloned at \
Stage 3 (not present here) and requires an LLM-judge API key on top of the \
agent's own OpenAI key. Wire the actual command from that repo's README here \
once Stage 3 succeeds."
}

stage_6_save_and_summarize() {
    log "=== Stage 6/6: save results + summary ==="
    python3 "$ROOT_DIR/results/summarize_fdb_v3.py"
}

trap 'kill "$(cat "$RAW_DIR/agent.pid" 2>/dev/null)" 2>/dev/null || true' EXIT

stage_1_install_deps
stage_2_check_env
stage_3_fetch_fdb_data
stage_4_launch_agent
stage_5_run_fdb_eval
stage_6_save_and_summarize
