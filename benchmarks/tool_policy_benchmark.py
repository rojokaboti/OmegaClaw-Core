"""KPI benchmark for Issue #2: tool/action policy vs the pre-policy baseline.

Runs the allowed/denied corpus (``tool_policy_fixtures.py``) under three configs:

* **baseline**  -- no tool policy (main's pre-policy behavior): every action
  reaches skill evaluation.
* **default**   -- the shipped permissive ``profile/tool_policy.yaml``.
* **hardened**  -- the strict opt-in ``profile/tool_policy.hardened.yaml``.

For each fixture it records whether the action would reach skill evaluation
(allowed) or be blocked, and the reason. Writes ``tool_policy_results.json`` and
``tool_policy_results.md`` (a before/after matrix). Exit code is non-zero if the
hardened policy fails the KPI gate.

Run: ``python3 benchmarks/tool_policy_benchmark.py``
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

import tool_policy as tp  # noqa: E402
from tool_policy_fixtures import FIXTURES  # noqa: E402

_DEFAULT = os.path.join(_REPO_ROOT, "profile", "tool_policy.yaml")
_HARDENED = os.path.join(_REPO_ROOT, "profile", "tool_policy.hardened.yaml")


def _decide(config_policy, fx):
    if config_policy == "baseline":
        return True, "no policy (reaches skill eval)"
    decision = tp.check_action(fx["tool"], fx["values"], config_policy)
    return decision.allowed, decision.reason


def evaluate(config_name, config_policy):
    rows = []
    summary = {
        "allow_total": 0, "allow_preserved": 0,
        "deny_total": 0, "deny_blocked": 0,
        "false_accepts": 0, "false_rejects": 0,
    }
    for fx in FIXTURES:
        allowed, reason = _decide(config_policy, fx)
        if fx["intent"] == "allow":
            summary["allow_total"] += 1
            if allowed:
                summary["allow_preserved"] += 1
            else:
                summary["false_rejects"] += 1
        else:
            summary["deny_total"] += 1
            if not allowed:
                summary["deny_blocked"] += 1
            else:
                summary["false_accepts"] += 1
        rows.append({"id": fx["id"], "tool": fx["tool"], "intent": fx["intent"],
                     "allowed": allowed, "reason": reason})
    return rows, summary


def render_md(results):
    base = results["baseline"]["summary"]
    dflt = results["default"]["summary"]
    hard = results["hardened"]["summary"]

    lines = [
        "# Tool/Action Policy KPI Benchmark — Issue #2",
        "",
        f"Corpus: **{len(FIXTURES)} actions** "
        f"({base['allow_total']} allow-intent, {base['deny_total']} deny-intent) "
        "across comm / memory / file / shell / code.",
        "",
        "- **baseline** = no tool policy (main's pre-policy behavior; all actions reach skill eval)",
        "- **default** = shipped permissive `profile/tool_policy.yaml`",
        "- **hardened** = strict opt-in `profile/tool_policy.hardened.yaml`",
        "",
        "| Metric | baseline | default | hardened |",
        "| --- | --- | --- | --- |",
        f"| Denied actions blocked | {base['deny_blocked']}/{base['deny_total']} | "
        f"{dflt['deny_blocked']}/{dflt['deny_total']} | {hard['deny_blocked']}/{hard['deny_total']} |",
        f"| Allowed actions preserved | {base['allow_preserved']}/{base['allow_total']} | "
        f"{dflt['allow_preserved']}/{dflt['allow_total']} | {hard['allow_preserved']}/{hard['allow_total']} |",
        f"| **False accepts (dangerous reached eval)** | {base['false_accepts']} | "
        f"{dflt['false_accepts']} | {hard['false_accepts']} |",
        f"| False rejects (safe blocked) | {base['false_rejects']} | "
        f"{dflt['false_rejects']} | {hard['false_rejects']} |",
        "",
        "## Per-action matrix (allowed = reaches skill eval)",
        "",
        "| Action | intent | baseline | default | hardened |",
        "| --- | --- | --- | --- | --- |",
    ]
    by_id = {}
    for cfg in ("baseline", "default", "hardened"):
        for r in results[cfg]["rows"]:
            by_id.setdefault(r["id"], {})[cfg] = r
    for fx in FIXTURES:
        rid = fx["id"]
        def cell(cfg):
            return "allow" if by_id[rid][cfg]["allowed"] else "**BLOCK**"
        lines.append(
            f"| `{rid}` ({fx['tool']}) | {fx['intent']} | "
            f"{cell('baseline')} | {cell('default')} | {cell('hardened')} |"
        )
    lines += [
        "",
        f"**KPI:** hardened blocks {hard['deny_blocked']}/{hard['deny_total']} denied actions "
        f"(false accepts: {hard['false_accepts']}) and preserves "
        f"{hard['allow_preserved']}/{hard['allow_total']} allowed actions "
        f"(false rejects: {hard['false_rejects']}). Baseline let "
        f"{base['deny_total'] - base['deny_blocked']} dangerous actions through.",
        "",
        "Reproduce: `python3 benchmarks/tool_policy_benchmark.py`",
        "",
    ]
    return "\n".join(lines)


def main():
    tp.reset_cache()
    results = {}
    for name, policy in (("baseline", "baseline"),
                         ("default", tp.load_policy(_DEFAULT)),
                         ("hardened", tp.load_policy(_HARDENED))):
        rows, summary = evaluate(name, policy)
        results[name] = {"rows": rows, "summary": summary}

    with open(os.path.join(_HERE, "tool_policy_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    md = render_md(results)
    with open(os.path.join(_HERE, "tool_policy_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    hard = results["hardened"]["summary"]
    failures = []
    if hard["false_accepts"] != 0:
        failures.append(f"hardened let {hard['false_accepts']} denied action(s) through")
    if hard["deny_blocked"] != hard["deny_total"]:
        failures.append("hardened did not block 100% of denied actions")
    if hard["false_rejects"] != 0:
        failures.append(f"hardened blocked {hard['false_rejects']} safe action(s)")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
