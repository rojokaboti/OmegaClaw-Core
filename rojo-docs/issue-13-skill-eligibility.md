# Change Report — Issue #13: Skill eligibility gates, allowlists & readiness diagnostics

**Branch:** `feat/skill-eligibility` (off `main`, which has #1–#11 merged)
**Issue:** #13 — "Add skill eligibility gates, allowlists, and dependency readiness diagnostics"
**Depends on:** #11 (SKILL.md loader, merged as PR #33). Second issue in the #11–#19 fan-out.

---

## 1. Why this change exists

#11 loads every valid `SKILL.md` bundle and only *parses* the eligibility metadata. So the
loader would happily advertise a skill that needs `pdftk`, an API key, or macOS on a box that
has none of them — the agent then tries it and fails. OpenClaw and Hermes both gate skills on
OS, required binaries, env/config presence, tool availability, and per-agent allow/deny. This
change adds that layer so the agent is **only ever advertised skills it can actually run**, and
every unavailable skill comes with concrete setup guidance — without ever printing a secret.

### Accuracy vetting (issue vs. reality)
- Accurate and correctly scoped: #11 explicitly deferred gating to #13 (the `Skill` dataclass
  already carries `platforms` / `required_environment_variables` / `metadata`).
- **Under-specified mappings I had to define** (OmegaClaw has no native "toolset"/"config"
  concept): a `toolset → LLM_COMMANDS` map (a toolset is satisfied when all its tools are
  permitted by `tool_policy` and not in `OMEGACLAW_DISABLED_TOOLS`); `requires.config` checked
  against a non-secret `config:` map in `skills.yaml` (credentials belong in env vars); OS
  tokens normalized (`linux` / `darwin`≡`macos` / `windows`).
- **Precedence** made explicit and tested: `disabled` denylist → `entries[name].enabled:false`
  → `enabled` allowlist miss → requirement gates, short-circuited by `always` / `entries.always`.

## 2. Before → after

| | Before (#11) | After (#13) |
|---|---|---|
| Skill advertised when prereqs missing | yes (all valid skills) | no — only eligible skills |
| Unavailable skill | invisible reason → failed action | concise `SKILL_UNAVAILABLE:` note + `skills doctor` remediation |
| OS / env / bin / config / toolset gates | none | one normalized schema, evaluated per skill |
| Allow/deny | `enabled`/`disabled` names (#11) | + per-skill `entries` overrides + `always` |
| Readiness diagnostics | none | `scripts/omegaclaw-skills doctor` (text + `--json`) |
| Secret exposure | n/a | only env-var *names*/presence ever shown — never values |

## 3. Files changed

| File | Change |
|---|---|
| `src/skill_policy.py` *(new, stdlib-only, self-testing)* | Normalizes OpenClaw + Hermes metadata into one requirement schema; `evaluate(skill, cfg, env)` → `Eligibility(eligible, reasons=[Reason(kind, detail, remediation)])` across OS/env/bins/anyBins/config/toolset with the documented precedence; `classify()` (cached by skill names + **relevant-env presence** + config/entries/disabled-tools fingerprint — never values); `doctor()` structured report; secret-safe throughout. |
| `src/skill_loader.py` | `catalogue_block()` now advertises **only eligible** skills (via new `eligible_skills()` + best-effort `_classify()`), appends a bounded, secret-free `SKILL_UNAVAILABLE:` note for blocked skills, and honors `OMEGACLAW_SKILLS_DEBUG=1` to show all. Fail-open: if `skill_policy` is unavailable, all skills are treated eligible. |
| `profile/skills.yaml` | New `config:` (feature-flag presence) and `entries:` (per-skill overrides) sections, documented with the precedence rules. |
| `scripts/omegaclaw-skills` *(new, argparse, subparsers)* | `doctor` subcommand: eligible / blocked (with per-reason remediation) / invalid bundles; `--json`; `--config`. Forward-compatible home for the #12 install lifecycle. |
| `benchmarks/skill_policy_{fixtures,benchmark}.py` + `_results.{md,json}` *(new)* | 18-fixture gate matrix (each gate, both directions, + precedence + secret case); KPI gate over classification accuracy, remediation coverage, secret leaks, and the runtime prompt-only-eligible check. |
| `Autotests/test_skill_policy.py` *(new)* + `run_mandatory` | 7 host tests: matrix classification, remediation coverage, strict no-secret logging, precedence, toolset↔`OMEGACLAW_DISABLED_TOOLS`, doctor structure, catalogue eligibility integration. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../src/skill_policy.py`. |
| `README.md`, `docs/tutorial-03-writing-a-custom-skill.md` | Document eligibility, `skills doctor`, and `OMEGACLAW_SKILLS_DEBUG`. |

## 4. KPI results (`benchmarks/skill_policy_results.md`)

18-fixture matrix through the real `skill_policy` + the `catalogue_block` runtime path.

| Metric | baseline (no gate) | candidate |
|---|---|---|
| Classification accuracy | 0.56 | **1.00** |
| False eligible (unrunnable advertised) | 8 | **0** |
| False blocked | 0 | **0** |
| Blocked without remediation | 8 | **0** |
| Secret-value leaks (reasons + prompt) | 0 | **0** |
| Prompt advertises only eligible skills | False | **True** |

Candidate classifies the matrix perfectly, attaches remediation to every blocked skill, leaks
no secret values, and advertises only eligible skills in the runtime prompt — vs the baseline
advertising all 8 unrunnable fixtures. `sys.exit(1)` on any regression. Satisfies the issue's
KPI acceptance gate.

## 5. End-to-end validation

- `python3 src/skill_policy.py` → self-tests pass.
- `python3 Autotests/test_skill_policy.py` → 7/7; `test_skill_loader.py` → 14/14 (eligibility
  active). Host regression sweep (`skill_policy, skill_loader, tool_policy, action_protocol`)
  → **75 passed**.
- `python3 benchmarks/skill_policy_benchmark.py` → `KPI GATE: PASSED`;
  `benchmarks/skill_loader_benchmark.py` (#11) still `KPI GATE: PASSED`.
- `scripts/omegaclaw-skills doctor [--json]` on a mixed corpus → eligible/blocked/invalid with
  remediation; verified a secret env value set at runtime never appears in the output.

## 6. Reviewer guide

```bash
git checkout feat/skill-eligibility
python3 src/skill_policy.py
python3 Autotests/test_skill_policy.py
python3 benchmarks/skill_policy_benchmark.py       # KPI GATE: PASSED

# Hand demo — doctor over a mixed corpus (eligible + blocked + invalid):
tmp=$(mktemp -d); mkdir -p "$tmp/skills"/{greet,pdf,bad}
printf -- '---\nname: greet\ndescription: greet\n---\nhi\n' > "$tmp/skills/greet/SKILL.md"
printf -- '---\nname: pdf\ndescription: fill\nrequired_environment_variables: [PDF_KEY]\n---\nx\n' > "$tmp/skills/pdf/SKILL.md"
printf -- '# no frontmatter\n' > "$tmp/skills/bad/SKILL.md"
printf -- 'version: 1\nroots: ["%s/skills"]\n' "$tmp" > "$tmp/skills.yaml"
python3 scripts/omegaclaw-skills doctor --config "$tmp/skills.yaml"
```

## 7. Risk / rollback
- **Additive + fail-open.** If `skill_policy` can't be imported or eligibility eval raises,
  `catalogue_block` treats every skill as eligible (best-effort) — the loop never breaks.
- **Behavior change:** skills with unmet prerequisites are no longer advertised by default
  (that is the point). `OMEGACLAW_SKILLS_DEBUG=1` restores show-all for debugging. A skill with
  no requirements is unaffected.
- **Secret-safe by construction:** only env-var names and presence booleans enter reasons,
  logs, and cache keys; the KPI + a unit test assert no value leaks.
- **Cheap + cached:** `classify()` caches by a fingerprint that includes *relevant* env-var
  presence, so it invalidates when a required var appears/disappears but doesn't rescan need-
  lessly each turn.
- Follow-up branch off `main`; open a PR against `rojokaboti/OmegaClaw-Core`. Next in the
  fan-out: #12 (install lifecycle) / #15 (plugins) / #19 (sandbox), then #14.
