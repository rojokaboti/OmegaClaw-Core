# Skill-Eligibility KPI Benchmark — Issue #13

Fixture matrix (`skill_policy_fixtures.matrix`) with one fixture per gate (OS / env / bins / anyBins / config / toolset) in both directions, plus the precedence cases (disabled / allowlist / entries / always) and a secret-value case, driven through the real `src/skill_policy.py` and the `skill_loader.catalogue_block` runtime path.

- **baseline** = no eligibility layer: every loaded skill is advertised (unrunnable skills reach the prompt); no remediation.
- **candidate** = per-fixture gating + prompt advertises only eligible skills, with secret-free remediation for the rest.

| Metric | baseline | candidate |
| --- | --- | --- |
| Fixtures in matrix | 18 | 18 |
| Classification accuracy (target 1.00) | 0.5556 | 1.0 |
| False eligible (unrunnable advertised) | 8 | 0 |
| False blocked | 0 | 0 |
| Blocked without remediation | 8 | 0 |
| Secret-value leaks (reasons + prompt) | 0 | 0 |
| Prompt advertises only eligible skills | False | True |

Candidate classifies **100%** of the matrix correctly (0 false-eligible, 0 false-blocked), attaches remediation to every blocked skill, leaks **0** secret values, and advertises only eligible skills in the runtime prompt — vs the baseline advertising all 8 blocked fixtures.

Reproduce: `python3 benchmarks/skill_policy_benchmark.py`
