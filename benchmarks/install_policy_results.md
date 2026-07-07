# Install-Policy KPI Benchmark — Issue #19

Corpus: **5 benign** + **3 HIGH-malicious** + **2 containment** fixtures (`install_policy_fixtures.build_corpus`) through the real installer + scanner.

- **baseline** = no scanner: malicious bundles install, nothing blocked on content.
- **candidate** = static scan + fail-closed policy (non-interactive denies HIGH findings).

| Metric | baseline | candidate |
| --- | --- | --- |
| Benign bundles installed | 5 | 5 |
| Benign false-positive block rate (target <= 0.10) | 0.0 | 0.0 |
| HIGH-severity malicious block rate (target 1.00) | 0.0 | 1.0 |
| Path/symlink escapes outside root (target 0) | 0 | 0 |
| Secret content leaks in findings/lock (target 0) | 0 | 0 |

Candidate blocks **100%** of HIGH-severity malicious bundles, contains **all** path/symlink escapes (0 outside-root files), leaks **0** secrets, and over-blocks benign bundles at **0%** (<= 10% target) — the baseline blocks none.

Reproduce: `python3 benchmarks/install_policy_benchmark.py`
