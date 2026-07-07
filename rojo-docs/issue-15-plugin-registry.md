# Change Report — Issue #15: Plugin & MCP-style tool registry

**Branch:** `feat/plugin-registry` (off `main`, which has #1–#13 + #12 merged)
**Issue:** #15 — "Add plugin and MCP-style tool registry for extensible capabilities"
**Depends on:** #11 (loader, merged). Fourth in the #11–#19 fan-out.

---

## 1. Why this change exists

Skills (#11) describe *how* to use capabilities; plugins *provide* them. Every new capability
was previously a core edit (e.g. `tavily-search` / `technical-analysis` wired straight into
`src/agentverse.py` + the four protocol surfaces). This adds a discovery/registration layer so
a plugin ships tools **and** skills via a manifest with **zero core runtime edits**.

### Accuracy vetting (issue vs. reality)
- Accurate. The hard part (same as #11): under the strict-JSON action protocol a tool must be
  in `helper.LLM_COMMANDS` + `action_protocol.ARG_SPEC` and have a MeTTa dispatch.
- **Design decision:** rather than dynamically mutating those core constants per plugin
  (fragile), plugin tools are invoked through ONE generic static tool,
  **`plugin-invoke <name> <arg>`** → `plugin_registry.invoke` — the same generic-dispatch
  pattern as `use-skill`, and exactly how MCP exposes tools (a generic call interface). The
  core tool set stays stable; adding plugin tools needs no per-tool protocol/MeTTa edits.
- Plugin **skills** reuse the existing `skill_loader` via `plugin_registry.skill_roots()`.
- Default `profile/plugins.yaml` has **no roots**, so the out-of-box toolset is unchanged (no
  pollution — the lesson from #12); a committed `plugins/example-calculator/` is the reference,
  disabled by default.

## 2. Before → after

| | Before | After |
|---|---|---|
| Add a tool | edit 4 core files (`getSkills` + MeTTa eqn + `LLM_COMMANDS` + `ARG_SPEC`) | drop a plugin dir + point `profile/plugins.yaml` at it — 0 core edits |
| Invoke a plugin tool | n/a | `plugin-invoke <name> <arg>` (generic dispatch → `invoke`) |
| Plugin-provided skills | n/a | discovered by the SKILL.md loader via `skill_roots()` |
| Disabled/broken plugin | n/a | contributes nothing; failure isolated + reported (`PLUGIN_LOAD_ERRORS`) |
| Advertising | n/a | compact `PLUGIN_TOOLS:` prompt catalogue |

## 3. Files changed

| File | Change |
|---|---|
| `src/plugin_registry.py` *(new, stdlib+PyYAML, self-testing)* | Config (`profile/plugins.yaml`), manifest discovery (root may be a plugin dir OR a parent of plugin dirs), validation, isolated entrypoint import (`SourceFileLoader`, containment-checked), tool + `skill_dirs` registration, `invoke` (redacted, best-effort), `catalogue_block` (`PLUGIN_TOOLS:` + `PLUGIN_LOAD_ERRORS:`), `skill_roots`, `requires` gating (env/bins/config), duplicate-tool + failing-import + bad-manifest isolation, mtime cache + `reset`. |
| `src/helper.py`, `src/action_protocol.py` | `plugin-invoke` added to `LLM_COMMANDS` / `ARG_SPEC` (`[("name","tool"),("arg","input","text")]`) and to `HIGH_RISK_TOOLS`. |
| `src/skills.metta` | `plugin-invoke` prose line + `(= (plugin-invoke $name $arg) (py-call (plugin_registry.invoke $name $arg)))`. |
| `src/tool_policy.py` | `plugin-invoke` default risk `high` (runs plugin code). |
| `src/loop.metta` | `getContext` gains a `plugin_registry.catalogue_block` py-call segment (paren-neutral vs main). |
| `src/skill_loader.py` | `_roots` also merges `plugin_registry.skill_roots()` (best-effort, import-light). |
| `lib_omegaclaw.metta` | Registers `./src/plugin_registry.py`. |
| `profile/plugins.yaml` *(new)* | Roots (empty default) / disabled / config. |
| `plugins/README.md` + `plugins/example-calculator/` *(new)* | Worked example: manifest + entrypoint (`calc` tool) + a bundled `arithmetic-helper` SKILL.md; disabled by default. |
| `benchmarks/plugin_registry_{fixtures,benchmark}.py` + `_results.{md,json}` *(new)* | Toy-plugin KPI gate. |
| `Autotests/test_plugin_registry.py` *(new)* + `run_mandatory` | 11 host tests. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../src/plugin_registry.py`. |
| `README.md` | Documents the registry, `plugin-invoke`, and the high-risk caveat. |

## 4. KPI results (`benchmarks/plugin_registry_results.md`)

| Metric | baseline | candidate |
|---|---|---|
| Core files edited to add a tool (target 0) | 4 | **0** |
| Plugins loaded from a manifest dir | 0 | **3** |
| Tools registered | 0 | **2** |
| Plugin tool invocation correct (`calc 2+3*4 = 14`) | False | **True** |
| Disabled plugin contributes nothing | False | **True** |
| Failing plugin isolated (others still load) | False | **True** |
| Duplicate tool name rejected | False | **True** |

A manifest-declared plugin adds a working, invocable tool with **0 core-file edits** (baseline
needs 4); disabled plugins contribute nothing; a failing plugin is isolated with a reported
error; duplicate tool names are rejected. `sys.exit(1)` on regression.

## 5. End-to-end validation

- `python3 src/plugin_registry.py` → self-tests pass.
- `python3 Autotests/test_plugin_registry.py` → 11/11 (discover/invoke, disabled, bad manifest,
  failing import, duplicate tool, requires-gate, entrypoint-escape, catalogue, plugin skill dir
  → loader, empty-config no-op, committed example plugin).
- `python3 benchmarks/plugin_registry_benchmark.py` → `KPI GATE: PASSED`.
- Wiring: `plugin-invoke` agrees across `LLM_COMMANDS`/`ARG_SPEC`/`ALLOWED_TOOLS`/`HIGH_RISK`,
  renders `((plugin-invoke "calc" "2+2"))`, advertises `plugin-invoke{name, arg}`; the example
  plugin's `calc` computes and its `arithmetic-helper` skill is discovered via the loader.
  #11/#13 skill gates still pass. MeTTa edits paren-neutral vs `main`.

## 6. Reviewer guide

```bash
git checkout feat/plugin-registry
python3 src/plugin_registry.py
python3 Autotests/test_plugin_registry.py
python3 benchmarks/plugin_registry_benchmark.py     # KPI GATE: PASSED

# Hand demo — the committed example plugin, enabled via a temp config:
python3 - <<'PY'
import os, sys, tempfile, yaml; sys.path.insert(0, "src")
import plugin_registry as pr
cfg = {"version": 1, "roots": [os.path.abspath("plugins/example-calculator")]}
p, tools, errs = pr.ensure_loaded(cfg)
print("tools:", sorted(tools), "| calc(2+3*4) =", tools["calc"].handler("2+3*4"))
print(pr.catalogue_block(cfg))
PY
```

## 7. Risk / rollback
- **Additive, default-off.** `profile/plugins.yaml` ships no roots → no plugins load → the
  default runtime toolset is byte-identical. The example plugin is committed source, disabled.
- **Failure-isolated.** Bad manifest / failing import / unmet requirement / duplicate tool →
  skipped with an actionable error; `invoke`/`catalogue_block`/`skill_roots` are best-effort and
  never break the loop.
- **One protocol surface + one MeTTa equation** (`plugin-invoke`), mirroring `use-skill`;
  classified high-risk so hardened deployments can deny it. Entrypoint path is containment-checked.
- Follow-up branch off `main`; PR against `rojokaboti/OmegaClaw-Core`. Next: #19 sandbox (builds
  on #12 lock/origin + this high-risk classification), then #14 workshop.
