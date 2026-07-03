# Session-Scoped Reasoning KPI Benchmark — Issue #8

Fixture dataset: **2 independent multi-turn games** (`metta_sessions_fixtures.GAMES`) driven through the real `src/metta_sessions.py`.

- **baseline** = stateless `(metta ...)`: nothing persists between calls (re-send every accumulated premise each turn; one global space, no isolation).
- **candidate** = the session store: premises added once and reused; sessions isolated by id.

| Metric | baseline | candidate |
| --- | --- | --- |
| Fact preservation across turns | 0.00 | **1.00** |
| Cross-session leakage (facts) | 3 | **0** |
| Premise transmissions (all turns) | 14 | 8 |
| **Premise re-send reduction** | — | **43%** |

The candidate preserves **100%** of session premises across turns with **zero** cross-session leakage, and cuts repeated premise re-transmission by **43%** (each fact is added once and replayed from the store, vs. the stateless baseline re-sending all accumulated premises every turn). Inference itself still runs through the real two-premise `(|- …)` path (validated in-container).

Reproduce: `python3 benchmarks/metta_sessions_benchmark.py`
