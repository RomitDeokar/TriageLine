# TriageLine — Theme 05: Interruptible Real-Time Agents

TriageLine has **three distinct pieces**. They are easy to conflate because
they share vocabulary (interruption, epoch, deliberation) — this README
keeps them separate on purpose.

| # | Piece | What it is | Where |
|---|---|---|---|
| A | **Internal practice/replay harness** | This kit's own scenario runner + scorer (`run_local.py`, `harness/`) driving `agent.agent:ParticipantAgent`, a rule-based dual-process agent. **Not FDB-v3.** No LLM in the loop. | repo root: `agent/`, `harness/`, `scenarios/` |
| B | **Official FDB-v3 benchmark integration** | Reproduction plumbing for the real [Full-Duplex-Bench v3](https://github.com/DanielLin94144/Full-Duplex-Bench) (`v3/` directory) against a **LiveKit** voice agent (Silero VAD, OpenAI Whisper STT, gpt-4o, OpenAI TTS — unmodified FDB-v3 template). | `livekit_agent/`, `run_fdb_v3.sh` |
| C | **Triage Line extension** | A roadside/incident-triage call flow that reuses the SAME LiveKit shell as B, but replaces gpt-4o tool-calling with the legacy dialogue → deliberation → commit-state-machine stack (`legacy/core/`). | `livekit_agent/triage_brain.py`, `livekit_agent/triage_livekit_agent.py`, `legacy/core/` |

## A. Internal practice/replay harness (`run_local.py`, `agent/`)

A dual-process agent (fast path <5 ms inline, slow path async ASR/vision/tools,
epoch-guarded coordination) scored against this kit's own scenario set —
**this is a practice/replay harness the team built for local iteration, not
the official FDB-v3 benchmark.**

```bash
pip install -r requirements.txt
python run_local.py --all --agent agent.agent:ParticipantAgent
```

Actually re-run this session: **89.1/100** across the 9 public scenarios
(pub_01–04, 08, 09 at 100.0; pub_05 audio_asr_ambiguity 53.8; pub_06
audio_disfluency 56.9; pub_07 visual_port_lookup 90.8). See
`Triage_Line_Theme05_Readiness_Report.md` for the verified run log.

Details, tool manifest, scoring rubric: `docs/PROTOCOL.md`, `docs/SCORING.md`,
`docs/TOOLS.md`.

## B. Official FDB-v3 benchmark integration (`livekit_agent/`, `run_fdb_v3.sh`)

Reproduction entrypoint for the actual FDB-v3 benchmark:

```bash
./run_fdb_v3.sh
```

- **Benchmark:** Full-Duplex-Bench, `v3` directory — github.com/DanielLin94144/Full-Duplex-Bench
- **Transport:** LiveKit (`livekit-agents` SDK, `AgentServer`/`AgentSession`)
- **Model/provider (this template):** Silero VAD, OpenAI Whisper (`whisper-1`) STT, OpenAI `gpt-4o` LLM, OpenAI `tts-1` TTS
- **Files:** `livekit_agent/cascaded_agent.py` (agent, copied byte-for-byte from FDB-v3's `v3/`, per `livekit_agent/SETUP.md`), `mock_apis.py`, `latency_injector.py`, `livekit_inference.py` (FDB's own headless test client)

`run_fdb_v3.sh` is a 6-stage script (install deps → check credentials →
fetch FDB-v3 data → launch agent → run official eval → save results) that
fails fast and logs the exact blocker instead of fabricating output. In
this sandbox it **stops at Stage 1** (`pip install livekit-agents` — no
PyPI egress). Stages 2–6 (LiveKit Cloud/OpenAI credentials, the FDB-v3
Google-Drive data release, a live eval run, and real benchmark
outputs/logs/results) have **not been executed here** — see
`results/raw/environment_check.log` and the readiness report.

## C. Triage Line extension (`livekit_agent/triage_brain.py`, `legacy/core/`)

Same LiveKit shell as B, second call flow: a roadside/incident-triage agent
whose "brain" is the legacy dialogue → deliberation → commit-state-machine
stack (Phases 1–5), not an LLM.

```bash
python3 livekit_agent/adapter_tests/test_4_triage_line_interruption.py
```

Actually re-run this session: **PASS** — breakdown report → propose →
pending confirmation; caller correction mid-prompt aborts the stale action
and re-deliberates against the new location; nothing auto-finalizes without
an explicit "yes"; `force_resolve_pending()` now wired to teardown so no
action is left non-terminal. Full legacy suite: **191/191 passed**.
Details and a full transcript: `legacy/docs/EXTENSION_DEMO_TRANSCRIPT.md`.

**Not verified:** `triage_livekit_agent.py` (the real LiveKit entrypoint)
against a live room — same credential/network blockers as B.

## Known simplifications / mocked components (all of A/B/C)

- Triage Line dispatch is an in-memory log, not a real CAD/tow/emergency API.
- Location/distance/vehicle extraction is regex/keyword based, not real NLU or geocoding.
- FDB-v3's 12 tools (`mock_apis.py`) are mocked, not real travel/finance/e-commerce backends.
- The internal harness's agent (A) uses no LLM at all — rule-based NLU + local ASR/CLIP.
- TTS sub-utterance progress in the LiveKit bridge is a 0/1 placeholder, not real timing.

## Layout

```
agent/, harness/, scenarios/, run_local.py     A: internal practice harness (this kit, unmodified)
livekit_agent/                                 B: FDB-v3 LiveKit integration; C: Triage Line bridge/agent
legacy/core/, legacy/docs/                     C's brain (dialogue/deliberation/commit) + demo transcript
run_fdb_v3.sh                                  B's official reproduction command
docs/                                          kit docs (PROTOCOL/SCORING/TOOLS) + this phase's ARCHITECTURE/DECK/SHOTLIST
Triage_Line_Theme05_Readiness_Report.md        scored, verified-only readiness report
```
