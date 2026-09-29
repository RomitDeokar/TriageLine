# Triage Line

Triage Line is a full-duplex, interruptible voice dispatch agent for
roadside-assistance / incident triage calls. It's built for the
"Interruptible Real-Time Agents — Full-duplex conversation agents with
deliberative reasoning skills" hackathon theme, and is designed to visibly
deliberate before taking any consequential action (like dispatching a tow
truck or escalating to emergency services), rather than acting on a single
LLM pass.

## Current implementation status

**Phase 1 — scaffolding.** Only the project structure, package layout, and
initial configuration exist. No dialogue, deliberation, commit, provider,
persistence, harness, reporting, or UI logic has been implemented yet. See
`docs/NOT_IMPLEMENTED.md` for the full list.

## Project structure

```text
triage-line/
├── core/            # reasoning core: dialogue, deliberation, commit state machine
├── providers/       # STT/LLM/TTS/AudioIO interfaces + mock/live implementations
├── persistence/      # database + repositories
├── harness/          # offline replay/evaluation harness
├── reporting/         # trial-result summary tables
├── ui/               # live dashboard (WebSocket event consumer)
├── config/            # environment-driven settings
├── tests/            # unit tests
├── docs/             # architecture, requirements, UI spec, commit mechanism
├── run_demo.py        # entry point (placeholder in Phase 1)
└── requirements.txt
```

The system is designed to eventually run fully offline against mocked
STT/LLM/TTS providers, with a real-provider adapter layer (LiveKit +
Deepgram/Groq/Rime, or OpenAI Realtime) that can be wired in later with API
keys.

## Setup

```bash
pip install -r requirements.txt
python run_demo.py
```

At this stage, `run_demo.py` only prints a Phase 1 status message and the
current configuration — it does not run an actual call.
