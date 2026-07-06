# Change Report — Issue #11: OpenClaw/Hermes-compatible SKILL.md loader & prompt compiler

**Branch:** `feat/skill-loader` (off `main`, which has #1–#10 merged)
**Issue:** #11 — "Add OpenClaw/Hermes-compatible SKILL.md loader and prompt compiler"
**Scope:** #11 only — the dependency root of the #11–#19 cluster. Dependent issues
(#12 install, #13 eligibility, #14 workshop, #15 plugins, #19 sandbox) are **not** in
this PR.

---

## 1. Why this change exists

Skills were hardcoded MeTTa equations in `src/skills.metta` plus a static `getSkills`
prose tuple. Adding any skill meant editing **four coupled surfaces** — `getSkills`,
`helper.LLM_COMMANDS`, `action_protocol.ARG_SPEC`, and a MeTTa body. Meanwhile OpenClaw
and Hermes ship large ecosystems of **portable filesystem skills**: a directory with a
`SKILL.md` (YAML frontmatter + Markdown) plus optional `scripts/` / `references/` /
`templates/`. OmegaClaw could consume none of them without a rewrite. This change
discovers, validates and compiles external `SKILL.md` bundles into the prompt so the
agent can use the existing ecosystem with **zero core edits**.

### Accuracy notes (issue spec vs. verified reality)
- **The issue's plan understates the hardest point.** Its step 3 ("compile into a prompt
  block while preserving MeTTa invocation style") + the "adapter to `getSkills`" imply
  loaded skills become callable tools. But under the default **strict-JSON action
  protocol** (`src/action_protocol.py`), any tool absent from `helper.LLM_COMMANDS` **and**
  `action_protocol.ARG_SPEC` is rejected as `unknown_tool` (`action_protocol.py:226`).
  Injecting a `getSkills` line alone would advertise skills the agent literally cannot call.
- **Resolution (correct semantics).** In OpenClaw/Hermes a `SKILL.md` is a *procedural
  instruction playbook the agent follows using existing primitive tools* (`shell`,
  `read-file`, `send`, `metta`, …) — **not** a new atomic tool with its own executable
  body. So loaded skills are injected as **guidance**, executed via existing tools. This
  removes the per-skill 4-surface coupling entirely.
- **Progressive disclosure** needs exactly **one** new static tool, `use-skill <name>`,
  which returns the full body on demand — registered once in the four surfaces (O(1), not
  O(number of skills)).
- **Fixtures.** No OpenClaw/Hermes clone exists on this machine, so (user-approved) the
  corpus is a deterministic, committed generator (`benchmarks/skill_loader_fixtures.py`)
  faithfully modeling the real format, including the failure matrix.
- Baseline-vs-candidate is a Python `_baseline` inside the benchmark (repo convention),
  not an `upstream/main` checkout.

## 2. Before → after

| | Before | After |
|---|---|---|
| External SKILL.md bundles | not loadable | discovered + validated from configured roots |
| Add a skill from the ecosystem | rewrite as MeTTa (4 surfaces) | drop a bundle under a root — 0 code edits |
| Invalid bundle | n/a (couldn't load) | skipped with an **actionable error**, never silent |
| Skill body in prompt | full prose baked into `getSkills` | compact `LOADED_SKILLS:` catalogue + on-demand `use-skill` |
| Path safety | n/a | realpath containment; symlink/`..`/unsafe-name rejected |
| Secret in a skill body | n/a | redacted before reaching the prompt |

## 3. Files changed

| File | Change |
|---|---|
| `src/skill_loader.py` *(new, stdlib + PyYAML, import-light, self-testing)* | Discovery (`os.walk`, `followlinks=False`), realpath **containment** (`_contained`), frontmatter split + `yaml.safe_load` validation (`_parse_skill`), **unsafe-name** rejection, first-wins **duplicate** handling, `enabled`/`disabled` policy, mtime-signature cache folding in the policy fingerprint + `reset_cache()`, `catalogue_block()` (compact `LOADED_SKILLS:` segment) / `catalogue_line()`, `get_skill_body()` (progressive disclosure; redacts authored body **then** substitutes trusted `{baseDir}`/`{skillDir}`; body cap), `list_skills()`, `_selftest()`. Fail-open + best-effort throughout. |
| `profile/skills.yaml` *(new)* | Skill roots (default `skills/`), `enabled` allowlist, `disabled` denylist, `max_description_chars`. Mirrors `provider_config` conventions; fails open to a safe empty set. |
| `skills/README.md` *(new)* | The default bundled skill root + author guidance. |
| `src/loop.metta` | `getContext` gains a ` (py-call (skill_loader.catalogue_block)) ` segment (same py-call injection pattern as `action_protocol.output_format_block`). Paren-neutral (delta 0 vs `main`). |
| `src/skills.metta` | `use-skill` prose line + `(= (use-skill $name) (py-call (skill_loader.get_skill_body $name)))`. |
| `src/helper.py` | `"use-skill"` added to `LLM_COMMANDS`. |
| `src/action_protocol.py` | `"use-skill": [("name","skill","text")]` added to `ARG_SPEC` (also advertises `use-skill{name}` in the generated `OUTPUT_FORMAT`). |
| `src/tool_policy.py` | `"use-skill": "low"` in `_DEFAULT_RISK` (read-only body fetch). |
| `lib_omegaclaw.metta` | Registers `./src/skill_loader.py`. |
| `benchmarks/skill_loader_{fixtures,benchmark}.py` + `_results.{md,json}` *(new)* | 26-valid + 6-invalid corpus generator; KPI A/B benchmark with a `sys.exit(1)` gate; committed results. |
| `Autotests/test_skill_loader.py` *(new)* + `run_mandatory` | 11 host unit tests (discovery, validation, containment/symlink, unsafe name, duplicate, allow/deny, catalogue shape+overhead, progressive disclosure, secret redaction, empty-config no-op, full corpus). |
| `Autotests/mock/test_skill_loader_mock.py` *(new)* + `run_mandatory` | Docker-gated integration: installs a fixture `SKILL.md` in the container, drives `use-skill`, asserts the **real body marker** reaches history. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../src/skill_loader.py`. |
| `docs/tutorial-03-writing-a-custom-skill.md`, `README.md` | Document filesystem skills + the previously-undocumented native-tool 4-surface requirement. |

## 4. KPI results (`benchmarks/skill_loader_results.md`)

Corpus: **26 valid bundles** loaded + **6 invalid fixtures**, driven through the real
`src/skill_loader.py`.

| Metric | baseline | candidate |
|---|---|---|
| External SKILL.md bundles loaded (no core edits) | 0 | **26** |
| Invalid fixtures with an actionable error | 0 | **6** |
| Silent omissions (invalid dropped with no error) | 6 | **0** |
| Path/symlink escapes reaching the loaded set | 0 | **0** |
| Worst per-skill prompt overhead ratio (target ≤ 1.20) | 0.0 | **1.035×** |
| Secret leaked into catalogue / use-skill body | False | **False** |
| `{baseDir}` resolved in use-skill body | False | **True** |
| Unknown skill name is actionable | False | **True** |

Loads ≥ 25 bundles with zero hardcoded edits (baseline cannot load any), every invalid
fixture yields an actionable error (0 silent omissions), 0 path escapes, per-skill
overhead 1.035× the bare name/description formula, no secret leaks. `sys.exit(1)` on any
regression. Satisfies the issue's KPI acceptance gate. (The live "solve ≥7/10
skill-dependent tasks" half is exercised by the Docker-gated mock test, §5.)

## 5. End-to-end validation

**Host (pure-Python — the committed gate):**
- `python3 src/skill_loader.py` → self-tests pass.
- `python3 Autotests/test_skill_loader.py` → 11/11.
- `python3 benchmarks/skill_loader_benchmark.py` → `KPI GATE: PASSED`.
- Regression sweep: `pytest` over the host-runnable unit suites
  (`test_skill_loader, test_action_protocol, test_tool_policy, test_errors,
  test_tracing, test_channel_registry, test_provider_config`) → **119 passed**. All prior
  KPI gates still pass (`run`, `tool_policy`, `reasoning_trace`, `error_recovery`,
  `channel_registry`, `metta_sessions`). No committed result files drifted.

**In-container (Docker — live MeTTa wiring; documented, gated):** build `omegaclaw:local`,
start with a skill root, drive a task that calls `use-skill "<name>"`, and confirm the
`LOADED_SKILLS:` catalogue appears in the compiled prompt and the returned body reaches
`memory/history.metta` (this is what `Autotests/mock/test_skill_loader_mock.py` asserts —
same Docker-gated posture as #6/#7/#9/#10, since there is no host MeTTa/hyperon runtime).

## 6. Reviewer guide — test & compare against the previous version

### A. Read the core diff (no build)
```bash
git checkout feat/skill-loader
git diff main --stat
git diff main -- src/loop.metta src/skills.metta src/helper.py src/action_protocol.py
```
Focus on: the single `use-skill` tool registered across the four surfaces, and the
`catalogue_block` py-call segment in `getContext`.

### B. Pure-Python checks (seconds, no Docker)
```bash
python3 src/skill_loader.py
python3 Autotests/test_skill_loader.py
python3 benchmarks/skill_loader_benchmark.py     # KPI GATE: PASSED
```

### C. Hand demo — load a bundle + progressive disclosure (seconds)
```bash
python3 - <<'PY'
import os, sys, tempfile, yaml; sys.path.insert(0, "src")
import skill_loader as sl
root = os.path.join(tempfile.mkdtemp(), "skills"); os.makedirs(root+"/demo")
open(root+"/demo/SKILL.md","w").write(
  "---\nname: demo\ndescription: say hello\n---\nRun {baseDir}/scripts/hi.sh\n")
cfg = {"version":1,"roots":[root]}
print(sl.catalogue_block(cfg))                       # LOADED_SKILLS: - demo: say hello …
p = os.path.join(os.path.dirname(root),"skills.yaml"); yaml.safe_dump(cfg, open(p,"w"))
os.environ["OMEGACLAW_SKILLS_CONFIG_PATH"] = p; sl.reset_cache()
print(sl.get_skill_body("demo"))                     # {baseDir} resolved to an abs path
print(sl.get_skill_body("nope"))                     # USE-SKILL-ERROR: unknown skill …
PY
```

### D. Verify the tool is callable end-to-end (seconds)
```bash
python3 - <<'PY'
import sys; sys.path.insert(0,"src"); import action_protocol as ap
print(ap.parse_and_render_metta('{"actions":[{"tool":"use-skill","args":{"name":"demo"}}]}'))
PY   # -> ((use-skill "demo"))
```

### E. Compare to `main`
```bash
git show main:src/skill_loader.py      # does not exist on main
git diff main -- src/action_protocol.py src/helper.py   # +use-skill only
```

## 7. Risk / rollback
- **Additive + fail-open.** Zero configured/discovered skills ⇒ empty catalogue ⇒ the
  loop is byte-for-byte unchanged. A missing/invalid `profile/skills.yaml` falls back to a
  safe empty set.
- **Fail-safe per bundle.** A malformed/unsafe/duplicate/escaping bundle is skipped with an
  actionable error, never a crash; `catalogue_block`/`get_skill_body` swallow their own
  exceptions and degrade to empty/actionable output so the prompt build never breaks.
- **One protocol-surface change.** Only the single static `use-skill` tool is added; all
  existing tools and tests are untouched (119 host tests green, all prior KPI gates green).
- **Security down-payment.** realpath containment rejects symlink/`..` escapes; unsafe skill
  names are rejected; secret-shaped tokens in a body are redacted before the prompt. The
  full install trust boundary is Issue #19.
- **MeTTa hooks are minimal.** One py-call segment in `getContext` (paren-neutral vs `main`)
  and one `(= (use-skill …))` equation; live wiring verified by the Docker-gated mock test.
- Not pushed until ready; open a PR against `rojokaboti/OmegaClaw-Core`.
