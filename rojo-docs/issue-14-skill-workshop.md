# Change Report — Issue #14: Governed Skill Workshop (agent-proposed skill creation/updates)

**Branch:** `feat/skill-workshop` (off `main`, which has #11–#13 + #12 + #15 + #19 merged)
**Issue:** #14 — "Add governed Skill Workshop for agent-proposed skill creation and updates"
**Depends on:** #11 (loader), #12 (install/lock/rollback), #13 (eligibility), #19 (scanner).
Last issue in the #11–#19 cluster.

---

## 1. Why this change exists

Skill reuse only compounds if the agent can capture repeated workflows into skills — but direct
writes are dangerous. This adds OpenClaw's **Skill Workshop**: the agent drafts a skill into a
controlled *pending* queue, and only an **explicit operator apply** ever changes active skills.
Durable learning without uncontrolled self-modification.

### Accuracy vetting + design
- The governance invariant ("existing active skills are never changed without explicit apply")
  is enforced **structurally**: the agent's only workshop tool, `propose-skill`, writes solely
  to the pending dir; there is **no agent tool that mutates the active skill root**. `apply` is
  operator-only (CLI). So the boundary can't be bypassed by the model.
- Maximal reuse of the merged machinery: `propose` validates via `skill_loader` and scans via
  `install_policy` (#19) → malformed/unsafe drafts are **quarantined on submission** (never
  applyable); `apply` commits via `skill_install.install` (re-validate + re-scan + path
  containment + lock/origin/trust) and snapshots the prior version for `rollback`.
- Pre-empted the recurring review themes: containment on support-file paths + proposal ids
  (`is_safe_skill_name`), no committed runtime artifacts (queue under `memory/skill-workshop/`,
  gitignored), fail-closed (unsafe → quarantined, apply refuses non-pending), structured results.

## 2. Before → after

| | Before | After |
|---|---|---|
| Capture a workflow as a skill | hand-edit `src/skills.metta` | `propose-skill` → review queue |
| Active-skill change | direct/manual | ONLY via operator `workshop apply` |
| Malformed / unsafe draft | n/a | **quarantined** on submit, never applyable |
| Audit / reverse | none | `workshop list/inspect`; `apply` snapshots → `rollback` |
| Agent self-modification | uncontrolled if allowed | structurally impossible (no active-root tool) |

## 3. Files changed

| File | Change |
|---|---|
| `src/skill_workshop.py` *(new, stdlib, self-testing)* | Proposal queue under `OMEGACLAW_WORKSHOP_DIR` (default `memory/skill-workshop/`): `propose` (stage → validate `skill_loader` → scan `install_policy` → pending/quarantined; path-contained support files), `propose_tool` (string bridge for the agent tool), `list_proposals`/`inspect`, `apply` (operator-only; snapshot + `skill_install.install`), `reject`/`quarantine`/`revise`, `rollback` (remove new / restore prior snapshot). |
| `src/helper.py`, `src/action_protocol.py` | `propose-skill` in `LLM_COMMANDS` / `ARG_SPEC` (`[("name","skill"),("body",…)]`). |
| `src/skills.metta` | `propose-skill` prose (when to propose) + `(= (propose-skill $name $body) (py-call (skill_workshop.propose_tool …)))`. |
| `src/tool_policy.py` + `profile/tool_policy.hardened.yaml` | `propose-skill` risk `low` (sandboxed to pending) + enabled under hardened default-deny. |
| `lib_omegaclaw.metta` | Registers `install_policy.py` + `skill_workshop.py`. |
| `scripts/omegaclaw-skills` | `workshop` nested subcommands: `list/inspect/apply/reject/revise/quarantine/rollback` (apply is the operator boundary; `--approve` for HIGH findings). |
| `benchmarks/skill_workshop_benchmark.py` + `_results.{md,json}` *(new)* | Governance KPI gate. |
| `Autotests/test_skill_workshop.py` *(new)* + `run_mandatory` | 9 host tests. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../src/skill_workshop.py`. |
| `.gitignore`, `README.md` | Ignore the runtime queue; document the workshop. |

## 4. KPI results (`benchmarks/skill_workshop_results.md`)

5 valid "captured workflow" proposals + 1 malformed + 1 unsafe.

| Metric | baseline | candidate |
|---|---|---|
| Valid proposals applied cleanly after review (≥ 4/5) | 0.0 | **1.00** |
| Active-skill changes BEFORE apply (target 0) | 0 | **0** |
| Malformed + unsafe proposals quarantined (not installed) | False | **True** |
| Quarantined proposal refuses to apply | False | **True** |
| Rollback restores prior state | False | **True** |

100% of valid proposals apply after review with **0** active-skill changes before apply;
malformed/unsafe drafts are quarantined and un-applyable; rollback restores. Satisfies the
issue's gate (no active change before approval, ≥4/5 valid proposals apply).

## 5. End-to-end validation

- `python3 src/skill_workshop.py` → self-tests pass (propose no-write, malformed/unsafe
  quarantine, apply, rollback, patch-restore, reject).
- `python3 Autotests/test_skill_workshop.py` → 9/9.
- `python3 benchmarks/skill_workshop_benchmark.py` → `KPI GATE: PASSED`; #11/#12/#13/#15/#19
  gates unaffected.
- CLI: `workshop list → apply → (active skill appears) → rollback → (removed)`; `propose-skill`
  renders `((propose-skill "greet" "…"))` and agrees across the four protocol surfaces.

## 6. Reviewer guide

```bash
git checkout feat/skill-workshop
python3 src/skill_workshop.py
python3 Autotests/test_skill_workshop.py
python3 benchmarks/skill_workshop_benchmark.py     # KPI GATE: PASSED

# Hand demo — propose (no active write) -> apply -> rollback:
tmp=$(mktemp -d); export OMEGACLAW_WORKSHOP_DIR="$tmp/ws"
printf 'version: 1\nroots: ["%s/installed"]\n' "$tmp" > "$tmp/skills.yaml"
python3 - <<PY
import os,sys; sys.path.insert(0,"src"); os.environ["OMEGACLAW_SKILLS_CONFIG_PATH"]="$tmp/skills.yaml"
import skill_workshop as sw
r=sw.propose("greet","---\nname: greet\ndescription: d\nversion: 1.0.0\n---\nSay hi.\n")
print("proposed:", r["id"], r["status"], "| active written?", os.path.isdir("$tmp/installed/greet"))
PY
python3 scripts/omegaclaw-skills --config "$tmp/skills.yaml" workshop list
```

## 7. Risk / rollback
- **Structurally safe:** the agent cannot write the active skill root (no such tool); only the
  operator CLI `apply` does. Zero active-skill changes before apply is enforced, not merely
  documented.
- **Fail-closed capture:** malformed/unsafe proposals are quarantined at submission and refuse
  to apply; `apply` re-scans via #19 and refuses anything non-pending.
- **Reversible:** every apply snapshots the prior version; `rollback` removes a new skill or
  restores the prior one (via the #12 installer).
- **No new runtime artifacts committed** (queue gitignored). Additive; default behavior is
  unchanged until an operator applies a proposal.
- Follow-up branch off `main`; PR against `rojokaboti/OmegaClaw-Core`. **This completes the
  #11–#19 skill/plugin/security/governance cluster.**
