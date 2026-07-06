# Skill-Loader KPI Benchmark — Issue #11

Corpus: **26 valid bundles** loaded + **6 invalid fixtures** (`skill_loader_fixtures.build_corpus`), driven through the real `src/skill_loader.py`.

- **baseline** = `asi-alliance` hardcoded skills (`src/skills.metta` + `getSkills`): 0 external filesystem bundles loadable without editing core files; no diagnostics for malformed bundles.
- **candidate** = discovery + validation + compact catalogue + progressive disclosure, with zero core edits.

| Metric | baseline | candidate |
| --- | --- | --- |
| External SKILL.md bundles loaded (no core edits) | 0 | 26 |
| Invalid fixtures with an actionable error | 0 | 6 |
| Silent omissions (invalid dropped with no error) | 6 | 0 |
| Path/symlink escapes reaching the loaded set | 0 | 0 |
| Worst per-skill prompt overhead ratio (target ≤ 1.20) | 0.0 | 1.035 |
| Secret leaked into catalogue | False | False |
| Secret leaked into use-skill body | False | False |
| {baseDir} resolved in use-skill body | False | True |
| Unknown skill name is actionable | False | True |

Loaded **26** valid bundles (≥ 25 target) with zero hardcoded edits; every one of the 6 invalid fixtures produced an actionable error (0 silent omissions); **0** path/symlink escapes reached the loaded set; worst per-skill catalogue overhead was **1.035×** the bare name/description formula (≤ 1.20 target); no secret leaked into the catalogue or a use-skill body.

Reproduce: `python3 benchmarks/skill_loader_benchmark.py`
