# Skill-Workshop KPI Benchmark — Issue #14

5 valid 'captured workflow' proposals + 1 malformed + 1 unsafe, through the real `src/skill_workshop.py`.

- **baseline** = no queue: new skills need manual `src/skills.metta` edits — no governed capture, quarantine, or rollback.
- **candidate** = propose→review→apply; the active root changes ONLY on explicit apply.

| Metric | baseline | candidate |
| --- | --- | --- |
| Valid proposals applied cleanly after review (>= 4/5) | 0.0 | 1.0 |
| Active-skill changes BEFORE apply (target 0) | 0 | 0 |
| Malformed + unsafe proposals quarantined (not installed) | False | True |
| Quarantined proposal refuses to apply | False | True |
| Rollback restores prior state | False | True |

Candidate applies **100%** of valid proposals after review with **0** active-skill changes before apply; malformed/unsafe proposals are quarantined (never installed) and cannot be applied; rollback restores prior state. The baseline has no such governance.

Reproduce: `python3 benchmarks/skill_workshop_benchmark.py`
