# Build Prompt: "Triage Line" — A Full-Duplex Deliberative Voice Dispatch Agent

Paste everything below into Claude Code (or Claude with code execution) as a single
message. It is written to be built in one pass by an agent with a Linux sandbox,
Python, and (optionally) a LiveKit + Deepgram/Groq/Rime or OpenAI Realtime stack.

---

## PROMPT TO PASTE

You are building a complete, runnable hackathon project for the theme:
**"Interruptible Real-Time Agents — Full-duplex conversation agents with
deliberative reasoning skills."**

### The product concept

Build **Triage Line**: a voice agent for a roadside-assistance / emergency
dispatch call center. A caller reports a breakdown or incident. The agent must:

1. Hold a real full-duplex conversation (listen while speaking, stop instantly
   when interrupted, resume intelligently rather than restarting).
2. Visibly **deliberate** before committing to a dispatch decision — severity
   triage, location confirmation, resource selection — not just call a
   function after one LLM pass.
3. Never dispatch a real unit based on something the caller didn't actually
   confirm hearing back, and never lose a genuine emergency to a race
   condition, a dropped call, or an interruption.

Do **not** reuse or reference any prior project's architecture, naming, or
file layout. Design this fresh, from these requirements only. In particular:
avoid the specific pattern of "gate a DB commit purely on a TTS word-timestamp
boundary" as the *only* integrity mechanism — that is one valid technique, but
this project's deliberation and confirmation model should be its own design,
not a copy of an existing "audio fence" concept. You have latitude to design
the actual mechanism; the requirements below are the contract it must satisfy.

### Hard requirements (must all work end-to-end)

**A. Full-duplex conversation**
- Real barge-in: the agent stops speaking within ~150ms of detected caller
  speech, not at the next sentence boundary.
- The agent can emit short backchannel acknowledgments *while the caller is
  still talking* (e.g. "mm-hm", "okay, go on") without waiting for end-of-turn,
  using interim/partial transcription.
- After an interruption, the agent must **resume intelligently**: either
  continue the interrupted thought, re-ask only the missing piece, or abandon
  it and follow the caller's new input — decided by the deliberation layer,
  not hardcoded.

**B. Deliberative reasoning layer (the centerpiece)**
Implement an explicit, inspectable reasoning loop that runs before any
consequential action (e.g. dispatching a tow truck, escalating to emergency
services, closing a case). At minimum it must:
- Produce a short structured "deliberation record" per decision: what is
  known, what is uncertain, what options were considered, why one was chosen,
  and a confidence level.
- Support **self-critique**: the agent checks its own proposed action against
  constraints (e.g. "is this location inside our service radius?", "does
  reported severity justify emergency escalation vs standard dispatch?")
  before acting, and can override its first instinct.
- Support **re-deliberation on new information**: if the caller corrects
  themselves mid-call ("actually it's not a flat tire, the car's on fire"),
  the agent must visibly re-run the relevant part of its reasoning, not just
  patch a database field silently.
- Persist every deliberation record (JSONL or DB) so it can be replayed and
  audited after the call — this is your evidence artifact for judges.

**C. Confirmation-safe commit mechanism**
Design your own mechanism (not copied from any reference project) that
guarantees: a dispatch action is only finalized once the system has
sufficient confirmed evidence that (a) the caller actually received/heard the
critical confirmation, and (b) no contradicting input arrived before that
point. State this mechanism explicitly as a small state machine with named
states and transitions, and justify each design choice in a short doc.

**D. Two comparable operating modes**
Build both a "naive" agent (commits immediately after the LLM decides, no
confirmation-safety) and the full "deliberative" agent, sharing the same
underlying tools/database, so they can be run head-to-head. Build a replay/
simulation harness that feeds both agents the same N scripted interruption
scenarios and records: commit/rollback outcome, whether it matched ground
truth, and latency. Target at least 100 trials per agent.

**E. Live demo visualization**
A terminal or lightweight web view that shows, in real time during a call:
current conversation turn, current deliberation state, and the dispatch
decision state machine transitioning live. This is what judges will actually
watch — prioritize it being legible and fast over being fancy.

**F. Reporting**
A script that summarizes the replay harness results into a clear pass/fail
table (naive vs deliberative: mismatch count, false-dispatch count,
missed-emergency count, latency percentiles) suitable for pasting into a
pitch deck.

### Tech constraints
- Python. Use `livekit-agents` if credentials are available; otherwise build
  the entire pipeline behind clean interfaces (STT/LLM/TTS as injectable
  abstract classes) so it runs fully offline against mocked/replayed audio
  and transcript events for development and grading, with a real-provider
  adapter that can be wired in with API keys later.
- No hardcoded secrets — env-driven config module.
- Include a proper test suite (pytest) for the deliberation state machine and
  the commit mechanism, with unit tests covering: normal confirmation,
  mid-confirmation interruption, caller self-correction, dropped
  call/session teardown, and race between two contradicting inputs.
- Include a README with: architecture diagram (ASCII is fine), how to run
  the offline replay harness, how to run the live dashboard, and how to plug
  in real LiveKit/Deepgram/Groq/Rime (or OpenAI Realtime) credentials.

### Reference documents

Three companion documents are provided alongside this prompt:
`REQUIREMENTS.md`, `ARCHITECTURE.md`, and `UI_SPEC.md`. Treat them as binding
specification, not inspiration — build exactly the layered, event-bus-driven
architecture described in `ARCHITECTURE.md` (core/providers/persistence/
harness/reporting/ui, decoupled via `core/events.py` and a WebSocket event
bus), implement every functional requirement in `REQUIREMENTS.md`, and build
the UI to the screens/components/event contract in `UI_SPEC.md`. If any
prompt text above and these documents ever conflict, the documents win — they
are the more detailed spec.

### Deliverable format
Produce the project as real files in a working directory, fully self-
contained and runnable with `pip install -r requirements.txt`. Do not just
describe the architecture — implement it. Also produce
`docs/COMMIT_MECHANISM.md`, documenting the confirmation-safe commit design
you chose, as referenced by `REQUIREMENTS.md` FR-3.2.

### Required final section: "What's NOT built yet"
At the end, output a clear, honest markdown section titled
`## Not implemented / next improvements` listing, in priority order, every
piece of the spec above that is stubbed, mocked, simplified, or skipped due
to time — and what real integration or hardening each would need. Be
specific (e.g. "backchanneling uses a fixed-delay heuristic, not real interim
transcript confidence scoring — needs streaming partial-STT wiring against a
live provider to be demo-real"). This section is what the team will use to
plan their remaining hackathon time — do not omit or soften it.

---

## Notes for whoever runs this prompt

- Point Claude Code at an empty directory and paste the block above as-is.
- If you have real LiveKit/Deepgram/Groq/Rime keys, mention that up front so
  Claude wires the real adapters instead of only the offline-mock path.
- Expect Claude to ask at most one clarifying question (e.g. real-provider
  keys available or not) — if it doesn't, it will default to the offline-
  mock path, which is the safer choice for guaranteed runnability during
  judging.
- Ask Claude Code to run its own test suite before declaring done, and to
  actually execute the replay harness once so `results/` has real numbers
  in it, not placeholders.
