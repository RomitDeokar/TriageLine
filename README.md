# TriageLine — Theme 05: Interruptible Real-Time Agents

A voice-native agent that keeps responding while it works, drops stale work the moment the user
corrects themselves, and never duplicates or silently loses a state change. It is scored on
**Full-Duplex-Bench v3** through LiveKit (the current Theme-05 guide, `docs/guides/`), plus one
extension use case, **Triage Line** (roadside / incident triage).

| What the guide scores | Where it is in this repo |
|---|---|
| **60% — FDB-v3 re-run by the organisers** | `./run_fdb_v3.sh` (one command) → custom LiveKit agent `livekit_agent/cascaded_agent.py` |
| **20% — extension use case** | **Triage Line**: `livekit_agent/triage_livekit_agent.py` (+ `triage_brain.py`, `legacy/core/`) |
| **20% — docs / architecture / video** | this README, [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (diagram), `docs/deck/`, `docs/VIDEO_SHOTLIST.md` |

Status and what is still left to do by a human with keys: [`docs/SUBMISSION_CHECKLIST.md`](docs/SUBMISSION_CHECKLIST.md).
Canonical documents are this README, `docs/ARCHITECTURE.md`, `docs/SUBMISSION_CHECKLIST.md`, `submission.yaml` and
`results/results.md`. Everything under `docs/archive/` is historical.


## Fast failure replay (diagnostic, seconds not hours)

A full live FDB-v3 run takes ~2 h; a fix must not be validated by one. This harness replays the
**live run's own transcripts and fragment timing** through the same adapter offline and diffs
expected vs live-recorded vs replayed tool calls:

```bash
python livekit_agent/replay_live_failures.py                  # table + failure classes
python livekit_agent/replay_live_failures.py --json out.json  # machine-readable
python livekit_agent/replay_live_failures.py --no-replay      # only diff the live recording
```

It resolves `$RESULT_n` references the way the official evaluator does. Measured on the same 29
live transcripts, fixing spelled letter-only ids (`track order B O B` -> `BOB`) moved pass
**41% -> 69%**, missing calls 7 -> 1, extra calls 4 -> 0, with the offline diagnostic held at 91/100.

## Submission (Samsung PRISM Generative AI Hackathon 2026 — Theme 05)

**Team Wizards · SRM Institute of Science and Technology (KTR, J1)** — Romit Deokar, Manmohan Singh, Rian K Sinu, Pragalbh Rai

Everything a reviewer needs is linked below. No file lives outside this repository; secrets are never committed.

| # | Required item | Where |
|---|---|---|
| 1 | **Working prototype code** (public repo) | <https://github.com/RomitDeokar/TriageLine> — Git tag **`PRISM_GENAI_HACKATHON_Y2026`** |
| 2 | **Requirements** | [`requirements.txt`](requirements.txt) · [`requirements-app.txt`](requirements-app.txt) (gateway + LiveKit workers) · [`requirements-fdb.txt`](requirements-fdb.txt) (official FDB-v3 runner) |
| 3 | **Presentation** (PPT) | [`docs/deck/SRM_Wizards_5_Submission.pptx`](docs/deck/SRM_Wizards_5_Submission.pptx) |
| 4 | **Demo video** (3–5 min, Drive) | <https://drive.google.com/file/d/1GDqNa02lEbf5xl4cgFHxh8mEMuRj_Doh/view?usp=sharing> |
| 5 | Supplementary clip (PWA interruption, 23 s) | [`docs/video/pwa_interrupt.mp4`](docs/video/pwa_interrupt.mp4) |
| 6 | **Detailed README** (this file) | setup, one-command reproduction, modes, deployment, results |
| 7 | Architecture & diagrams | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| 8 | Deployment guide (temporary + permanent) | [`docs/DEPLOY.md`](docs/DEPLOY.md) |
| 9 | Benchmark evidence (runs, logs, evaluators) | [`results/results.md`](results/results.md) · [`results/20260930_live_run/`](results/20260930_live_run/) · [`results/20260929T084428Z/`](results/20260929T084428Z/) |
| 10 | One-command reproduction (FDB-v3) | `./run_fdb_v3.sh` (see "Quick start"); diagnostic offline mode `./run_fdb_v3.sh --offline-text` |
| 11 | Video shot list (what the demo shows) | [`docs/VIDEO_SHOTLIST.md`](docs/VIDEO_SHOTLIST.md) |
| 12 | AI usage disclosure | [`docs/Wizards_AI_Disclosure.docx`](docs/Wizards_AI_Disclosure.docx) |
| 13 | Submission checklist (organiser items) | [`docs/SUBMISSION_CHECKLIST.md`](docs/SUBMISSION_CHECKLIST.md) |
| 14 | Integrity (no benchmark memorisation) | `python scripts/integrity_audit.py --strict-comments` → PASS; held-out set `scenarios_heldout/` 29/30 |

Honest scoping: the scored live FDB-v3 number is produced by the **organisers' common re-run** with their pinned LLM judge; the numbers in this repo are our own runs, each labelled with its mode (live exact-match / offline diagnostic / practice kit).


## Architecture (one diagram)

```
caller / input.wav ─▶ Silero VAD ─▶ STT (Deepgram nova-3 streaming | Gemini) ─┐
                                                                              ▼
            ┌───────────── TriageAdapter (livekit_agent/adapter.py) ─────────────┐
            │ commit gate (merges endpointer-split finals) · VAD/partial barge-in │
            │ every tool call = its own asyncio task (never blocks speech)        │
            └──────────────┬──────────────────────────────────▲─────────────────┘
                   events  ▼                                  │ tool_call / cancel_tool
            ┌──────────── ParticipantAgent (agent/agent.py, agent/nlu.py) ──────────┐
            │ FAST  repair-aware parsing · revise / retract / switch · acknowledgement │
            │ SLOW  tools, chained clauses bound to earlier results                    │
            │ COORD epoch version → stale results dropped · in-flight ledger ·         │
            │       operation ledger (no duplicate commit, no auto-retry of unknown)   │
            └──────────────┬──────────────────────────────────────────────────────┘
                           ▼ speech                   official FDB-v3 mock_apis.py (fresh per room)
                  TTS (Deepgram aura-2 | Gemini) ─▶ agent audio
```

Full mermaid diagram, turn-taking details, and the cancellation trade-off: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## 1. FDB-v3 benchmark (scored 60%)

### Reproduce (organisers)

```bash
# Python 3.10-3.12, ffmpeg, git. Fill ONE file: livekit_agent/.env.local (template: livekit_agent/.env.example)
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET     LiveKit Cloud project (free Build plan)
#   DEEPGRAM_API_KEY  or  GEMINI_API_KEY                 speech (Deepgram preferred: streaming)
#   OPENAI_API_KEY                                       the official gpt-4o judge only
PYTHON=python3.12 ./run_fdb_v3.sh --limit 5 --require-judge   # smoke run
PYTHON=python3.12 ./run_fdb_v3.sh --require-judge             # full scored run
```

Stages: venv + pinned deps (`requirements-fdb.txt`) + NeMo ASR + LiveKit plugin weights → credentials (repo-root
`.env` and `livekit_agent/.env.local`; the latter wins) + a **real TTS→STT round trip** with the selected speech
models → clone FDB-v3 at the pinned commit + download the official data → start the worker and wait for
registration → the official runner and evaluators → artefacts in `results/<UTC stamp>/` and `results/results.md`.

The run **fails loudly**, never scores a partial run, when any of these happen:
- the runner exits non-zero
- a result file is missing, stale, or has a status other than `completed` (the official runner also writes
  `inference_failed` / `no_output`)
- the agent dies
- the judge is unreachable under `--require-judge`
- any single argument/response check fell back to exact match (`scripts/judge_coverage.py`)

It deletes this provider's earlier `result_triageline.json` / audio in the data dir before inference, so an old
result can never be scored. Keep them with `KEEP_OLD_RESULTS=1` (without `--force`).

`./run_fdb_v3.sh --offline-text` runs without keys: official transcripts → the same adapter/agent → the official
evaluators. It is a diagnostic only.

### Scored configuration (pinned by the script, recorded in `results/<stamp>/run_config.json`)

| Stage | Component | Runs |
|---|---|---|
| VAD | Silero (`livekit-plugins-silero`), semantic turn detector (`livekit-plugins-turn-detector`) | local |
| STT | `auto`: **Deepgram `nova-3` (streaming)** if `DEEPGRAM_API_KEY`, else Gemini `gemini-3.5-flash-lite` | hosted |
| Understanding + tool calls | `ParticipantAgent`, **rules only** (`TRIAGELINE_LLM_PLANNER=0`, `TRIAGELINE_LLM_MODE=fallback`): deterministic given the transcript | local |
| Tools | official FDB-v3 `mock_apis.py` at commit `3e799c45` (byte-identical, copied at run time), new registry per room | local |
| TTS | `auto`: **Deepgram `aura-2`** if keyed, else Gemini `gemini-3.8-flash-lite-tts` | hosted |
| Judge | official evaluators, OpenAI `gpt-4o` | hosted |

Provider name `triageline` → `result_triageline.json`. Gemini model names change often: the speech preflight
catches a wrong name before inference. Override with `TRIAGELINE_STT_MODEL` / `TRIAGELINE_TTS_MODEL`
(e.g. `gemini-2.5-flash` on older projects). One free Gemini key serving both STT and TTS for 100 recordings may
hit rate limits, so prefer Deepgram ($200 signup credit) for the scored run.

### Numbers (every number states its mode)

| Mode | Strict pass | Notes |
|---|---|---|
| **Live LiveKit, exact match, judge off** (final, 99/100 turn-taken) | **28/100** | `results/live_20260930T200524Z/` — tool-sel 80.6%, arg-acc 39.7%, latency 5.82 s; exact match is a harsh lower bound (run #1 was 13/100) |
| **Live LiveKit, judged** (what is scored) | **organisers re-run** | no OpenAI key is provided to teams (confirmed by the organisers); their common re-run supplies the pinned judge |
| Offline text replay (official transcripts → adapter → official evaluator, judge off) | **91/100** | diagnostic upper bound; no audio, no STT |
| Practice kit (9 public scenarios, local Whisper ASR, offline by default) | **87/100** | `run_local.py --all`; set `TRIAGELINE_LOCAL_USE_ENV=1` only if you want the live planner |
| Independent held-out paraphrase set (`scenarios_heldout/`, rules only) | **29/30** | never used to tune rules |

Audio replay with a small offline Whisper model has scored well below the text replay (≈50/100 before the
current fixes). Most losses there are STT errors on spelled ids and fillers, which is why the live run uses a
hosted streaming STT biased with the tool vocabulary. The live number is the only one that counts.

### Integrity (guide §6: no memorising benchmark items)

- Rules are schema-driven (argument names, types, enums, descriptions of whatever manifest is loaded).
  Nothing keys off scenario ids, timestamps, expected strings or benchmark wording.
- `python3 scripts/integrity_audit.py --strict-comments` scans the decision code, comments, docstrings and all
  test fixtures for every 5-word sequence of the 100 public utterances and every distinctive expected argument
  value. It passes. Before 2026-09-29 the audit read the wrong metadata field (0 utterances), so its earlier PASS
  was vacuous. That is fixed, and every overlapping fixture was paraphrased.
- Overfitting caveat: rules were developed against the public set, so the text-replay number is optimistic. The
  mitigations are the held-out paraphrase set, the strict audit, and the absence of any item-keyed logic.

## 2. Extension use case (scored 20%): Triage Line — roadside / incident triage

A caller reports a breakdown or incident. The agent asks only what it needs (location, hazards, injuries),
**deliberates** before any consequential action, and puts every action (tow dispatch, emergency escalation)
through a two-phase commit: `PROPOSED → PENDING_CONFIRMATION → FINALIZED / ABORTED`. A correction mid-prompt
("no, I'm past exit 12") aborts the stale proposal and re-deliberates. Nothing finalizes without an explicit
"yes". On any disconnect `force_resolve_pending()` leaves no action non-terminal. It runs in the same LiveKit
shell as the benchmark worker. **All actions are simulated** (in-memory dispatch log).

```bash
python3 livekit_agent/adapter_tests/test_4_triage_line_interruption.py   # correction aborts + re-deliberates
python3 livekit_agent/adapter_tests/test_8_triage_confirmation_safety.py # confirmation-safety probes E-01..E-07
# live, end to end (LiveKit keys + one speech key):
TRIAGELINE_AGENT_NAME=triageline-triage python livekit_agent/triage_livekit_agent.py dev
TRIAGELINE_TRIAGE_AGENT_NAME=triageline-triage python -m ui     # open /rtc.html?flow=triage, choose "Triage Line"
```

The gateway maps the closed choice `flow=triage` to `TRIAGELINE_TRIAGE_AGENT_NAME`. The client never names an
agent. Transcript of a full call: `legacy/docs/EXTENSION_DEMO_TRANSCRIPT.md`.

## 3. Supporting infrastructure (not scored separately)

- **Gateway + clients** (`ui/api.py`, `python -m ui`): `/rtc.html` (LiveKit WebRTC voice, both call types),
  `/live.html` (PWA: text, clips, camera), `/console` (evaluation console), `POST /api/mobile/token` for native
  apps. Production hardening, env vars (`CORS_ORIGINS`, `TRIAGELINE_ACCESS_CODE`, …) and the token flow:
  [`docs/MOBILE_INTEGRATION.md`](docs/MOBILE_INTEGRATION.md).
- **Phone assistant mode** (`TRIAGELINE_MODE=assistant`): the same worker asks for missing details and reads back
  every LLM-proposed state change before acting. There the planner defaults to `TRIAGELINE_LLM_MODE=primary`
  (Gemini → Cerebras → OpenRouter → Mistral failover, T=0, seed 7, schema-validated).
- **Internal practice harness** (older Theme-05 kit, not scored under the current guide): `run_local.py`,
  `harness/`, `scenarios/`. `python run_local.py --all --agent agent.agent:ParticipantAgent` reports 87/100
  on the 9 practice scenarios (default `--agent` is the kit's baseline).
- Free keys, step by step: [`docs/FREE_API_KEYS.md`](docs/FREE_API_KEYS.md).

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-fdb.txt -r requirements-app.txt pytest
.venv/bin/python -m pytest tests livekit_agent/adapter_tests -q      # 215 passed; 12 environment-only failures (project .env sets providers, TestClient version)
bash tests/shell/test_run_fdb_v3_failures.sh                          # run-script failure detection
(cd ui && npm ci && npm test)                                         # 10 passed
.venv/bin/python scripts/heldout_eval.py                              # 29/30
.venv/bin/python scripts/integrity_audit.py --strict-comments         # PASS
.venv/bin/python -m ui --offline                                      # key-free app on :8080
```

CI: `docs/ci.github-workflow.yml` runs all of the above (copy it to `.github/workflows/ci.yml`; the bot token cannot push workflow files).

## Known simplifications

- All tools are simulated: FDB-v3's 12 mocks, and Triage Line's in-memory dispatch log. There is no real
  CAD/tow/emergency integration, and this must not be used as an emergency service.
- Location/vehicle extraction in Triage Line is regex/keyword based, with no geocoding.
- The gateway keeps sessions process-local (one replica). The access code is pilot access, not user identity.
- The LiveKit path has been exercised offline (adapter tests, offline replay, real SDK imports). A live judged
  run needs the team's own keys; see the checklist.
