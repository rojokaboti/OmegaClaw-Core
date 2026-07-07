# Skill-Install KPI Benchmark — Issue #12

Corpus: **10 local** + **10 git** sources (git repos created locally — real `git`, no network; ClawHub HTTP is covered by `Autotests/test_skill_install.py`), driven through the real `src/skill_install.py`.


- **baseline** = no install lifecycle (0 installs, no lockfile).
- **candidate** = fetch→validate→commit→lock with idempotent reinstall, pinning, verify, rollback.

| Metric | baseline | candidate |
| --- | --- | --- |
| Install sources (local + git) | 20 | 20 |
| Install success rate (target >= 0.95) | 0.0 | 1.0 |
| Lock-metadata coverage (target 1.00) | 0.0 | 1.0 |
| Duplicate dirs after reinstall (target 0) | 0 | 0 |
| Pinned skipped by update --all | False | True |
| Pinned bytes unchanged after update --all | False | True |
| verify: all installed skills OK | False | True |
| Rollback leaves root unchanged on bad source | False | True |

Candidate installs **100%** of the corpus, records complete lock metadata for **100%** of skills, produces **0** duplicate dirs on reinstall, protects pinned skills from `update --all`, and rolls back cleanly on an invalid source — none of which the baseline can do.

Reproduce: `python3 benchmarks/skill_install_benchmark.py`
