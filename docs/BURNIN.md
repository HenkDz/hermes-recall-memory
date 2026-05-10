# Recall burn-in results

Operator burn-in runs are synthetic, isolated, and credential-free. They validate archive scale behavior without mutating the active Hermes profile database.

## 2026-05-10 — v0.3.4 100k archive burn-in

Command used from `/mnt/e/Projects/AI/hermes-recall-memory`:

```bash
python /tmp/recall_100k_fast_burnin.py | tee /tmp/recall_100k_fast_burnin.json
```

Result: **PASS**

Summary:

| Check | Result |
| --- | ---: |
| Observations inserted | 100,000 |
| Episodes inserted | 1,200 |
| Audit events appended | 3,000 |
| Audit chain | OK |
| Diagnose | OK |
| FTS search result count | 20 |
| Quality rank result count | 50 |
| Consolidation suggestion count | 1 |
| Redaction-at-rest leaks | 0 |
| SQLite DB bytes | 152,956,928 |
| WAL bytes | 23,418,112 |
| Total runtime | 189.277s |

Detailed timings:

- Bulk observations: 178.203s
- Episodes: 3.464s
- Audit: 6.195s

Audit head:

```text
ec13a567edde2a9448c402a49326ec2bd20f362b413dbbe8188f54f6854032d5
```

Notes:

- The burn-in used a temporary SQLite database under `/tmp/recall-100k-burnin-*` and did not touch the active `recall-test` profile database.
- Synthetic secret-shaped values were inserted and verified redacted at rest across observations, episodes, and audit previews.
- The generic `scripts/recall_stress_probe.py` remains useful for broad operator checks, but its per-row commit path is intentionally heavier; use a batched burn-in harness for 100k+ scale checks.
