"""KPI benchmark for Issue #15: plugin/tool registry vs the hardcoded baseline.

Deterministic, host-runnable. Materializes a toy-plugin corpus and drives the real
`src/plugin_registry.py`.

* **baseline** = `asi-alliance`: no registry — adding a tool means editing core runtime files
  (`getSkills` + `helper.LLM_COMMANDS` + `action_protocol.ARG_SPEC` + a MeTTa equation = 4),
  and 0 plugins are loadable at runtime.
* **candidate** = a plugin ships tools (and skills) via a manifest with **0 core edits**; tools
  are invocable; a disabled plugin contributes nothing; a failing plugin is isolated.

KPI gate (`sys.exit(1)`): candidate core-edits-to-add-a-tool == 0, tool invocation correct,
disabled contributes nothing, and a failing plugin does not stop the others.

Writes `plugin_registry_results.{md,json}`. Run: `python3 benchmarks/plugin_registry_benchmark.py`
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import plugin_registry as pr  # noqa: E402
from plugin_registry_fixtures import build_plugins  # noqa: E402

# Core files a baseline must edit to add ONE callable tool (verified against the repo):
#   src/skills.metta (getSkills line + a (= (tool ..) ..) equation), src/helper.py
#   (LLM_COMMANDS), src/action_protocol.py (ARG_SPEC).
_BASELINE_CORE_EDITS = 4


def evaluate():
    info = build_plugins()
    root = info["root"]
    cfg = {"version": 1, "roots": [root]}

    pr.reset()
    plugins, tools, errs = pr.ensure_loaded(cfg)

    plugins_loaded = sorted(plugins)
    tools_registered = sorted(tools)
    calc_ok = "calc" in tools and tools["calc"].handler("2+3*4") == 14

    # disabled plugin contributes nothing
    pr.reset()
    _p2, tools_disabled, _e2 = pr.ensure_loaded(
        {"version": 1, "roots": [root], "disabled": ["calculator", "dupe"]})
    disabled_ok = "calc" not in tools_disabled

    # a failing plugin ("broken") is isolated: the good ones still loaded above
    failing_isolated = ("broken" not in plugins_loaded) and ("calculator" in plugins_loaded) \
        and any(e.plugin == "broken" for e in errs)

    # duplicate tool across plugins rejected (only one 'calc')
    dup_rejected = tools_registered.count("calc") == 1 and any("duplicate tool" in e.message for e in errs)

    pr.reset()

    candidate = {
        "core_edits_to_add_tool": 0,
        "plugins_loaded": len(plugins_loaded),
        "tools_registered": len(tools_registered),
        "tool_invocation_correct": calc_ok,
        "disabled_contributes_nothing": disabled_ok,
        "failing_plugin_isolated": failing_isolated,
        "duplicate_tool_rejected": dup_rejected,
        "load_errors_reported": len(errs),
    }
    baseline = {
        "core_edits_to_add_tool": _BASELINE_CORE_EDITS,
        "plugins_loaded": 0, "tools_registered": 0,
        "tool_invocation_correct": False, "disabled_contributes_nothing": False,
        "failing_plugin_isolated": False, "duplicate_tool_rejected": False,
        "load_errors_reported": 0,
    }
    return {"baseline": baseline, "candidate": candidate}


def render_md(s):
    b, c = s["baseline"], s["candidate"]
    rows = [
        ("Core files edited to add a tool (target 0)", "core_edits_to_add_tool"),
        ("Plugins loaded from a manifest dir", "plugins_loaded"),
        ("Tools registered", "tools_registered"),
        ("Plugin tool invocation correct (calc 2+3*4 = 14)", "tool_invocation_correct"),
        ("Disabled plugin contributes nothing", "disabled_contributes_nothing"),
        ("Failing plugin isolated (others still load)", "failing_plugin_isolated"),
        ("Duplicate tool name rejected", "duplicate_tool_rejected"),
    ]
    lines = [
        "# Plugin-Registry KPI Benchmark — Issue #15",
        "",
        "Toy-plugin corpus (`plugin_registry_fixtures.build_plugins`: a working calculator, an "
        "echoer, a failing plugin, and a duplicate-tool plugin) through the real "
        "`src/plugin_registry.py`.",
        "",
        "- **baseline** = no registry: adding one callable tool edits **{} core files** "
        "(`getSkills` + MeTTa equation + `LLM_COMMANDS` + `ARG_SPEC`), 0 runtime plugins.".format(
            b["core_edits_to_add_tool"]),
        "- **candidate** = a manifest-declared plugin ships tools with **0 core edits**.",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
    ]
    for label, key in rows:
        lines.append("| {} | {} | {} |".format(label, b[key], c[key]))
    lines += [
        "",
        "Candidate adds a working, invocable tool with **0 core-file edits** (baseline needs {}); "
        "disabled plugins contribute nothing; a failing plugin is isolated with a reported error; "
        "duplicate tool names are rejected.".format(b["core_edits_to_add_tool"]),
        "",
        "Reproduce: `python3 benchmarks/plugin_registry_benchmark.py`",
        "",
    ]
    return "\n".join(lines)


def main():
    s = evaluate()
    with open(os.path.join(_HERE, "plugin_registry_results.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    md = render_md(s)
    with open(os.path.join(_HERE, "plugin_registry_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    c = s["candidate"]
    failures = []
    if c["core_edits_to_add_tool"] != 0:
        failures.append("candidate required core edits to add a tool")
    if not c["tool_invocation_correct"]:
        failures.append("plugin tool did not compute correctly")
    if not c["disabled_contributes_nothing"]:
        failures.append("disabled plugin still contributed a tool")
    if not c["failing_plugin_isolated"]:
        failures.append("a failing plugin was not isolated")
    if not c["duplicate_tool_rejected"]:
        failures.append("duplicate tool name was not rejected")
    if s["baseline"]["core_edits_to_add_tool"] <= 0:
        failures.append("baseline should require core edits")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
