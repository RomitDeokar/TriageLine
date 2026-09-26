# TriageLine — Theme 05: Interruptible Real-Time Agents

TriageLine has **three distinct pieces**. They are easy to conflate because
they share vocabulary (interruption, epoch, deliberation) — this README
keeps them separate on purpose.

| # | Piece | What it is | Where |
|---|---|---|---|
| A | **Internal practice/replay harness** | This kit's own scenario runner + scorer (`run_local.py`, `harness/`) driving `agent.agent:ParticipantAgent`, a rule-based dual-process agent. **Not FDB-v3.** No LLM in the loop. | repo root: `agent/`, `harness/`, `scenarios/` |
| B | **Official FDB-v3 benchmark integration** | Reproduction plumbing for the real [Full-Duplex-Bench v3](https://github.com/DanielLin94144/Full-Duplex-Bench) (`v3/` directory) against a **custom LiveKit voice agent** (Silero VAD → hosted STT → rule-based `ParticipantAgent`, **no LLM** → hosted TTS). | `livekit_agent/`, `run_fdb_v3.sh` |
| C | **Triage Line extension** | A roadside/incident-triage call flow that reuses the SAME LiveKit shell as B, but uses the legacy dialogue → deliberation → commit-state-machine stack (`legacy/core/`). | `livekit_agent/triage_brain.py`, `livekit_agent/triage_livekit_agent.py`, `legacy/core/` |

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

```bash
./run_fdb_v3.sh --offline-text      # no keys: official data + official evaluators (text replay)
./run_fdb_v3.sh --limit 5           # live LiveKit smoke run
./run_fdb_v3.sh                     # full scored run (LLM judge on)
```

### Model / provider declaration (custom agent)
| Stage | Component | Where it runs |
|---|---|---|
| VAD | Silero (`livekit-plugins-silero`) | local |
| STT | `TRIAGELINE_STT_PROVIDER`: OpenAI `whisper-1` (default) · Groq `whisper-large-v3-turbo` · Deepgram `nova-3` | hosted |
| Understanding + tool calls | `ParticipantAgent` (`agent/agent.py`, `agent/nlu.py`): rule-based, schema-driven, epoch-guarded. **No LLM.** | local |
| Tools | official FDB-v3 `mock_apis.py` (pinned commit, unmodified) | local |
| TTS | `TRIAGELINE_TTS_PROVIDER`: OpenAI `tts-1`/nova (default) · Deepgram `aura-2-andromeda-en` | hosted |
| Judge (evaluation only) | official evaluators, OpenAI `gpt-4o` | hosted |

- **Provider name:** `triageline` → `result_triageline.json` (offline mode: `triageline_text`)
- **Pinned:** FDB-v3 commit `3e799c45`, `requirements-fdb.txt` (livekit-agents 1.8.3), Python 3.10–3.12, CLIP HF revision. The agent is deterministic (no sampling).
- **Free keys:** step-by-step guide in [`docs/FREE_API_KEYS.md`](docs/FREE_API_KEYS.md) (LiveKit Build plan + Groq + Deepgram).

`run_fdb_v3.sh` stages: venv + pinned deps → credentials → clone the pinned FDB-v3 commit + `gdown` the data → start `cascaded_agent.py` and wait for registration → official `run_tool_benchmark_all_released.py` + `evaluate_tool_calls.py` / `evaluate_pass_rate.py` / `analyze_tool_latency.py` → artefacts in `results/<timestamp>/` + `results/results.md`. The judge is preflighted: if gpt-4o is unreachable, the run falls back to exact match and says so.

### Current numbers (offline text replay, official data + official evaluators, exact match)
**Strict pass 85/100 (85.0%)**, tool-selection 95.5%, argument accuracy 89.5%. Dev split 83.0% · held-out split 87.2% (`livekit_agent/fdb_split.py`). See `results/results.md`. The live-audio run needs your own LiveKit keys.

**Anti-overfitting statement:** lexicons are generic (no benchmark-specific literals). Fixes target failure *classes* (repair markers, clause splitting, schema defaults), never individual items. The split is a fixed SHA-256 hash of the scenario id, and held-out results are reported, not debugged.

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
action is left non-terminal. Full legacy suite: **209 passed**. Confirmation-safety probes E-01..E-07: `adapter_tests/test_8`.
Details and a full transcript: `legacy/docs/EXTENSION_DEMO_TRANSCRIPT.md`.

Live: `python livekit_agent/triage_livekit_agent.py console` (or `dev` + LiveKit Agents Playground) with the keys from `docs/FREE_API_KEYS.md`.

## Known simplifications / mocked components (all of A/B/C)

- Triage Line dispatch is an in-memory log, not a real CAD/tow/emergency API.
- Location/distance/vehicle extraction is regex/keyword based, not real NLU or geocoding.
- FDB-v3's 12 tools (`mock_apis.py`) are mocked, not real travel/finance/e-commerce backends.
- No component uses an LLM at runtime; the agent is rule-based NLU (+ local ASR/CLIP for harness A).
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
