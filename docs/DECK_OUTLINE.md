# Deck Outline — 8 Slides

1. **Problem**
   Real-time voice agents must handle barge-ins without acting on stale
   reasoning, and must never take a consequential action (dispatch,
   booking, escalation) without explicit confirmation. Theme 05 asks for
   an interruptible real-time agent; we build and verify three layers:
   an internal practice harness, an FDB-v3 LiveKit integration, and a
   Triage Line extension on the same shell.

2. **Architecture**
   LiveKit Agent → Voice I/O → Fast/Slow Path → Epoch-Guarded
   Coordination → Interruption/Cancellation → Tool Adapter → (FDB-v3 |
   Triage Line → Deliberation → Commit State Machine). See
   `docs/ARCHITECTURE.md` for the full diagram and what's real vs. mocked
   at each layer.

3. **FDB-v3 benchmark**
   Target: Full-Duplex-Bench v3 (github.com/DanielLin94144/Full-Duplex-Bench),
   via a LiveKit agent (Silero VAD, OpenAI Whisper STT, gpt-4o, OpenAI
   TTS). Reproduction: `./run_fdb_v3.sh`. **Status, verified this session:
   stops at Stage 1/6 (no PyPI egress to install `livekit-agents`)** —
   official benchmark outputs are unverified in this environment. State
   this plainly; do not present benchmark numbers that were never produced.

4. **Interruption/recovery mechanism**
   Epoch counter bumped on every invalidation; in-flight tool calls
   tagged with the epoch they started under; stale completions dropped;
   targeted `cancel_tool` for in-flight work; operation ledger prevents
   duplicate state-modifying calls. Same mechanism reused, unmodified,
   across both the internal harness and the LiveKit adapter
   (`livekit_agent/adapter.py`).

5. **Technical implementation**
   `agent/agent.py` (`ParticipantAgent`, fast/slow path, epoch guard),
   `livekit_agent/adapter.py` (FDB-v3 tool adapter, reuses the same
   agent), `livekit_agent/triage_brain.py` (Triage Line adapter, wires
   `legacy/core/dialogue` → `deliberation` → `commit` in as the LLM seam).

6. **Triage Line extension**
   Roadside/incident-triage call flow on the same LiveKit shell.
   Dialogue engine → deliberation engine (intent match + self-critique
   constraints) → commit state machine
   (`PROPOSED → PENDING_CONFIRMATION → FINALIZED/ABORTED`, confirmation
   required, never skipped). `force_resolve_pending()` now wired to
   session teardown so no action is left non-terminal.

7. **Demo / real vs. simplified**
   Real: turn-taking, barge-in detection, deliberation, supersession,
   commit lifecycle, teardown handling — all exercised by a passing
   automated test. Simplified/mocked: dispatch is an in-memory log (no
   real CAD/API), location/distance extraction is regex-based (no
   geocoding), FDB-v3's 12 tools are mocked APIs, no live LiveKit room
   has been exercised in this sandbox.

8. **Results + next steps**
   Verified this session: internal harness 89.1/100 (9 public scenarios);
   interrupt/async/dedup unit tests pass; Triage Line extension test
   passes end-to-end (interruption, re-deliberation, confirmation safety,
   teardown); full legacy suite 191/191 passed. Not yet verified: official
   FDB-v3 run, live LiveKit room for either B or C. Next steps: run
   `run_fdb_v3.sh` somewhere with network + credentials + the FDB-v3 data
   release, then a live-room smoke test for both agents.
