# Video Shot List — 3–5 minute demo

Uses only scenarios that have actually been run and passed in this
sandbox. No live LiveKit room segment is included, since that has not
been exercised here — if recorded later (with real credentials), insert
it as an additional segment and label it clearly as a live run.

| # | What to record | Order | Duration | What the audience should observe |
|---|---|---|---|---|
| 1 | Title + architecture diagram (`docs/ARCHITECTURE.md`) | 1st | 20s | The three pieces (internal harness / FDB-v3 / Triage Line) and where they fork at the Tool Adapter. |
| 2 | Terminal: `python run_local.py --all --agent agent.agent:ParticipantAgent` | 2nd | 40s | The internal harness scoring live; call out this is the practice harness, **not** FDB-v3. Land on the 89.1/100 summary line. |
| 3 | Terminal or console UI: **FDB-v3 scenario to demonstrate — `scenarios/pub_02_text_interrupt.json`** (Boston→NYC interruption) run via `run_local.py --scenario scenarios/pub_02_text_interrupt.json` | 3rd | 40s | **Interruption moment**: agent mid-tool-call for Boston, user revises to NYC; epoch bump, `cancel_tool` for the stale call, second call issued with updated destination, no stale Boston result reaches the final response. |
| 4 | Terminal: `./run_fdb_v3.sh` | 4th | 20s | The script fails fast at Stage 1/6 with a clear logged blocker (no PyPI egress) — say plainly this is what "unverified" looks like, not a fabricated pass. |
| 5 | Terminal: `python3 livekit_agent/adapter_tests/test_4_triage_line_interruption.py` — **Triage Line scenario: breakdown at Highway 9, corrected to Highway 12** | 5th | 60s | **Re-deliberation moment**: after the correction, the stale Highway-9 action is aborted (`superseded_by_redeliberation`) and a new Highway-12 proposal appears. **Action/confirmation moment**: the Highway-12 action sits in `PENDING_CONFIRMATION` (dispatch log empty) until the scripted "Yes, go ahead" — then `FINALIZED` and logged. |
| 6 | Terminal: same test's teardown block + `cd legacy && python3 tests/_run_all.py` tail | 6th | 30s | `force_resolve_pending()` force-aborting a dangling action on disconnect; full legacy suite 191/191 passed. |
| 7 | Closing slide: results + what's simplified/unverified (deck slide 7–8) | 7th | 30s | Honest recap: what's real, what's mocked, what's unverified (official FDB-v3 run, live LiveKit room). |

**Total: ~4 minutes.** If time is tight, cut segment 1 to 10s and fold it
into narration over segment 2's terminal output.
