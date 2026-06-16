"""KPI benchmark for Issue #1: baseline parser vs JSON action protocol.

Runs the same fixture corpus (``benchmarks/fixtures.py``) through:

  * baseline  -> ``helper.balance_parentheses`` (the original repo behavior)
  * candidate -> ``action_protocol.parse_and_render_metta`` in strict ``json`` mode

and records parse success rate, validation rejection rate, false accepts
(unknown tool reaching the eval stream), false rejects (a legitimate action
lost), and the NOTHING_WAS_DONE count. Writes ``results.json`` and
``results.md`` next to this script so the comparison is reproducible.

Run: ``python3 benchmarks/run_benchmark.py``
Exit code is non-zero if the candidate fails the KPI acceptance gate.
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

import action_protocol as ap  # noqa: E402
from helper import balance_parentheses  # noqa: E402
from fixtures import FIXTURES  # noqa: E402

ALLOWED = ap.ALLOWED_TOOLS


def extract_tools(rendered):
    """Return the ordered tool names of the top-level commands in an
    s-expression string like ``((send "x") (pin "y"))``. Quotes are respected
    so parens inside a string argument (e.g. a metta expr) are ignored."""
    tools = []
    if not rendered or rendered[0] != "(":
        return tools
    depth = 0
    i = 0
    n = len(rendered)
    in_str = False
    esc = False
    while i < n:
        ch = rendered[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
            if depth == 2:
                j = i + 1
                while j < n and rendered[j] in " \t":
                    j += 1
                k = j
                while k < n and rendered[k] not in ' \t()"':
                    k += 1
                token = rendered[j:k]
                if token:
                    tools.append(token)
        elif ch == ")":
            depth -= 1
        i += 1
    return tools


def evaluate(parser_fn):
    """Run a parser over the corpus and return (per_fixture_rows, summary)."""
    rows = []
    summary = {
        "total": len(FIXTURES),
        "execute_total": 0,
        "execute_success": 0,
        "reject_total": 0,
        "reject_success": 0,
        "false_accepts": 0,   # unknown tool emitted into eval stream (unsafe)
        "false_rejects": 0,   # a legit expected action was lost
        "nothing_was_done": 0,
        "parse_failures": 0,  # fixtures whose desired outcome was not achieved
    }

    for fx in FIXTURES:
        rendered = parser_fn(fx["raw"])
        is_cmd = bool(rendered) and rendered.startswith("(") and rendered != "()"
        tools = extract_tools(rendered) if is_cmd else []
        known = [t for t in tools if t in ALLOWED]
        unknown = [t for t in tools if t not in ALLOWED]

        if not is_cmd:
            summary["nothing_was_done"] += 1

        has_false_accept = bool(unknown)
        if has_false_accept:
            summary["false_accepts"] += 1

        if fx["intent"] == "execute":
            summary["execute_total"] += 1
            expected = fx["expected_tools"]
            got_expected = expected.issubset(set(known))
            ok = got_expected and not unknown
            if ok:
                summary["execute_success"] += 1
            else:
                summary["parse_failures"] += 1
            if not got_expected:
                summary["false_rejects"] += 1
            outcome = "ok" if ok else "fail"
        else:  # reject
            summary["reject_total"] += 1
            ok = not is_cmd  # correctly produced no executable command
            if ok:
                summary["reject_success"] += 1
            else:
                summary["parse_failures"] += 1
            outcome = "ok" if ok else "fail"

        rows.append(
            {
                "id": fx["id"],
                "category": fx["category"],
                "intent": fx["intent"],
                "outcome": outcome,
                "tools": tools,
                "unknown_leaked": unknown,
                "rendered": rendered if len(rendered) <= 160 else rendered[:157] + "...",
            }
        )

    summary["parse_success_rate"] = round(
        (summary["execute_success"] + summary["reject_success"]) / summary["total"], 4
    )
    summary["execute_success_rate"] = (
        round(summary["execute_success"] / summary["execute_total"], 4)
        if summary["execute_total"]
        else None
    )
    summary["reject_success_rate"] = (
        round(summary["reject_success"] / summary["reject_total"], 4)
        if summary["reject_total"]
        else None
    )
    return rows, summary


def _baseline(raw):
    return balance_parentheses(raw)


def _candidate_json(raw):
    os.environ["OMEGACLAW_ACTION_PROTOCOL"] = "json"
    return ap.parse_and_render_metta(raw)


def _candidate_auto(raw):
    os.environ["OMEGACLAW_ACTION_PROTOCOL"] = "auto"
    return ap.parse_and_render_metta(raw)


def render_markdown(base, cj, ca):
    def pct(x):
        return f"{x * 100:.1f}%" if isinstance(x, float) else "n/a"

    lines = [
        "# Action Protocol KPI Benchmark — Issue #1",
        "",
        f"Corpus: **{base['total']} synthetic LLM outputs** across "
        "valid_json, legacy_text, malformed_json, unknown_tool, multiline_send, "
        "file_ops, metta_expr.",
        "",
        "- **Baseline** = `helper.balance_parentheses` (original repo behavior)",
        "- **Candidate (json)** = strict JSON mode, the shipping default. Legacy "
        "text is deliberately rejected (model is re-prompted for JSON), which is "
        "why some legacy-text fixtures count as parse failures here.",
        "- **Candidate (auto)** = JSON with legacy fallback — the migration path.",
        "",
        "| Metric | Baseline | Candidate (json) | Candidate (auto) |",
        "| --- | --- | --- | --- |",
        f"| Overall parse success rate | {pct(base['parse_success_rate'])} | {pct(cj['parse_success_rate'])} | {pct(ca['parse_success_rate'])} |",
        f"| Execute success rate | {pct(base['execute_success_rate'])} | {pct(cj['execute_success_rate'])} | {pct(ca['execute_success_rate'])} |",
        f"| Reject (validation) success rate | {pct(base['reject_success_rate'])} | {pct(cj['reject_success_rate'])} | {pct(ca['reject_success_rate'])} |",
        f"| Parse failures (count) | {base['parse_failures']} | {cj['parse_failures']} | {ca['parse_failures']} |",
        f"| **False accepts (unknown tool → eval)** | {base['false_accepts']} | {cj['false_accepts']} | {ca['false_accepts']} |",
        f"| False rejects (lost legit action) | {base['false_rejects']} | {cj['false_rejects']} | {ca['false_rejects']} |",
        f"| NOTHING_WAS_DONE outcomes | {base['nothing_was_done']} | {cj['nothing_was_done']} | {ca['nothing_was_done']} |",
        "",
    ]

    def reduction_line(name, cand):
        if base["parse_failures"]:
            r = (base["parse_failures"] - cand["parse_failures"]) / base["parse_failures"]
            return (
                f"- **{name}: parse-failure reduction {r * 100:.1f}%** "
                f"({base['parse_failures']} → {cand['parse_failures']})."
            )
        return ""

    lines.append(reduction_line("json", cj))
    lines.append(reduction_line("auto", ca))
    lines.append(
        f"- **Unsafe unknown-tool accepts: baseline {base['false_accepts']} → "
        f"json {cj['false_accepts']}, auto {ca['false_accepts']}.**"
    )
    lines.append("")
    lines.append("Reproduce: `python3 benchmarks/run_benchmark.py`")
    lines.append("")
    return "\n".join(lines)


def main():
    base_rows, base = evaluate(_baseline)
    cj_rows, cj = evaluate(_candidate_json)
    ca_rows, ca = evaluate(_candidate_auto)

    out = {
        "fixtures": len(FIXTURES),
        "baseline": base,
        "candidate_json": cj,
        "candidate_auto": ca,
        "baseline_rows": base_rows,
        "candidate_json_rows": cj_rows,
        "candidate_auto_rows": ca_rows,
    }
    with open(os.path.join(_HERE, "results.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    md = render_markdown(base, cj, ca)
    with open(os.path.join(_HERE, "results.md"), "w", encoding="utf-8") as f:
        f.write(md)

    print(md)

    # KPI acceptance gate (evaluated on the shipping strict-json default).
    failures = []
    if cj["parse_failures"] >= base["parse_failures"]:
        failures.append(
            f"candidate parse_failures ({cj['parse_failures']}) not below "
            f"baseline ({base['parse_failures']})"
        )
    if cj["false_accepts"] > base["false_accepts"]:
        failures.append("candidate accepts more unknown tools than baseline")
    if cj["false_accepts"] != 0:
        failures.append(f"candidate still accepts {cj['false_accepts']} unknown tool(s)")

    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
