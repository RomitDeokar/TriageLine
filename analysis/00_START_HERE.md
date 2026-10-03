# TriageLine — Score-Maximization Package (READ ME FIRST)

**Who this is for:** you (the human) and whatever AI you use later to apply the fixes.
**What this is:** a diagnosis of *why the online/live score is low*, and a prioritized list of
changes that raise it — each written as a **task card** with the exact file, the exact logic, and
the exact code shape to add. **Nothing in this package edits the agent.** You apply the changes.

---

## The 4 files, in reading order

| # | File | What it is | Read it when |
|---|---|---|---|
| 0 | `00_START_HERE.md` | this file — orientation + the one-page summary | now |
| 1 | `README_ANALYST_GUIDE.md` | vocabulary, how scoring works, how to reproduce, the "ground rules" for a weak AI | before touching anything |
| 2 | `01_DIAGNOSTIC_REPORT.md` | the **full bug / wrong-logic catalogue**, ranked by points, each with root cause + evidence + fix logic | to understand *why* |
| 3 | `02_FIX_SPECS.md` | the **fix task cards** (FC-01 … FC-xx) with exact code, in priority order, each independently testable | to *do* the work |
| 4 | `03_SCORE_MAXIMIZATION.md` | tactics beyond bug-fixing: turn-take, latency, safety, judge-quality, submission/reproducibility, runbook | for the last 15–25% |

---

## The one-page summary (if you read nothing else)

**Diagnosis:** the project is *not* a bad agent — it is a good agent whose score is capped by four
things, in this order:

1. **Turn-take dropouts (biggest single lever, and the most invisible).**
   The official metric scores `tool-selection` and `argument-accuracy` **only on samples where the
   agent took the turn**. In the archived live runs turn-take was **63/100** and then **85/100**
   (15 rooms lost to worker deaths). Every no-response sample silently zeroes a whole scenario.
   A room that never answers costs ~3× more than a wrong argument.
   → Fixing reliability/turn-take is worth **more than any parser change.**

2. **Argument accuracy (the biggest *content* lever).** ~71/100 samples had an imperfect argument
   in the archived live run; final code is at ~58.8%. Losses cluster in: missing `max_price`,
   singular/plural `query`, wrong entity picked after a self-correction, currency direction, and
   `order_id = "ID"` (a cue word parsed as the id).

3. **Extra/duplicate tool calls.** ~30/100 samples emitted a spurious call (`search_flights`,
   `search_products`, `update_search_filter`, `calculate_commute`). Each extra call drops the
   precision part of tool-selection → scenario fails even when the right call happened.

4. **Reported numbers are stale.** `results/results.md` and the `results/*` folders still show
   **13/100**; the current commit message reports **42/100**. Anyone scoring your submission reads
   the *older, worse* number first. **This is a free, immediate points gain — update the evidence
   in the README to the current run, clearly labelled.**

**Realistic ceiling:** arguments + turn-take + duplicate suppression are worth roughly
**+20 to +35 strict-pass points** on the exact-match lower bound, which maps to a *much* larger
gain once the organisers' gpt-4o judge (semantic argument matching) is applied.

---

## How to use this with a weak AI

Give the AI **one task card at a time** from `02_FIX_SPECS.md`. Each card has:
`Symptom → Root cause (file:line) → Exact change → Test that must pass → Don't-break`.
Tell the AI: *"implement FC-0X exactly; do not touch other files; run the listed test."*
Never let it edit more than one card per commit. Do **not** paste the whole report and say
"fix everything" — that is how a weak model breaks working code.

---

## Ground rules (do not violate these — the guide disqualifies them)

- **No hardcoding benchmark items.** No scenario ids, no expected strings, no "if the city is X".
  All logic must be schema-driven or lexicon-driven (the repo already follows this; keep it).
- **No calling your own servers at eval time.** All logic lives in the submission.
- **No caching across scenarios.** Every room starts fresh (the code already resets state per room).
- **Test on a clean machine.** If `./run_fdb_v3.sh` doesn't reproduce on the organisers' box, the 60%
  benchmark portion scores **zero**. This is the single most catastrophic risk in the whole project —
  see `03_SCORE_MAXIMIZATION.md §Reproducibility`.
