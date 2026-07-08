# Session-Store KPI Benchmark — Issue #16

Synthetic corpus of **1000 sessions** (each tagged with a unique keyword; half interrupted with a resumable snapshot) + 1 secret-canary session, through the real `src/session_store.py`.

- **baseline** = raw `history.metta`/trace logs: no session index, no FTS, no resume.
- **candidate** = SQLite + FTS5 search + resumable snapshots + redaction.

| Metric | baseline | candidate |
| --- | --- | --- |
| Sessions indexed | 1000 | 1000 |
| recall@5 (target >= 0.90) | 0.0 | 1.0 |
| Avg search latency ms over 1k corpus (target < 500) | None | 0.133 |
| Resume success (target >= 0.80) | 0.0 | 1.0 |
| Secret leaks in search/export (target 0) | 0 | 0 |

Candidate indexes **1000** sessions, finds the right session at recall@5 **1.00** in **0.13 ms** avg, resumes **100%** of interrupted fixtures, and leaks **0** secrets — the baseline requires manual log parsing.

Reproduce: `python3 benchmarks/session_store_benchmark.py`
