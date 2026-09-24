# Triage Line Evaluation

Trials: 128

## DELIBERATIVE AGENT

| Metric | Value |
|---|---|
| Trials | 128 |
| Successful trials | 128 |
| Failed trials | 0 |
| Deliberations | 192 |
| Re-deliberations | 64 |
| Constraint failures | 84 |
| Actions proposed | 108 |
| Actions finalized | 64 |
| Actions aborted | 44 |
| Unsafe finalizations | 0 |
| Barge-ins | 1 |
| Matches ground truth | 128 |
| Mismatches | 0 |
| False dispatches | 0 |
| Missed emergencies | 0 |

## NAIVE AGENT

| Metric | Value |
|---|---|
| Trials | 128 |
| Successful trials | 128 |
| Failed trials | 0 |
| Deliberations | 0 |
| Re-deliberations | 0 |
| Constraint failures | 0 |
| Actions proposed | 128 |
| Actions finalized | 128 |
| Actions aborted | 0 |
| Unsafe finalizations | 128 |
| Barge-ins | 1 |
| Matches ground truth | 43 |
| Mismatches | 85 |
| False dispatches | 64 |
| Missed emergencies | 21 |

## Comparison

| Metric | Naive | Deliberative |
|---|---|---|
| Mismatches | 85 | 0 |
| False dispatches | 64 | 0 |
| Missed emergencies | 21 | 0 |
| Unsafe finalizations | 128 | 0 |

**Result: PASS**

- deliberative agent had zero unsafe finalizations, false dispatches, and missed emergencies