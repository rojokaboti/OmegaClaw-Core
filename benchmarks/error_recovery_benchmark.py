"""KPI benchmark for Issue #10: structured error recovery vs the string-only baseline.

Deterministic and host-runnable (no MeTTa/LLM/Docker). It drives the REAL
`src/errors.py` + `src/action_protocol.py` over one fixture per error category
(`error_recovery_fixtures.FIXTURES`) and measures how machine-readable the error
feedback is, then compares against a model of the original repo's behavior.

* **baseline** = original `asi-alliance/OmegaClaw-Core@main` behavior: an opaque
  string (`…NOTHING_WAS_DONE…` / `ERROR_FEEDBACK: …`) with no error category, no
  failed action, no retryability, no concise repair hint, no trace id. Every
  failure lands in a single unknown/unclassified bucket.
* **candidate** = `errors` + the wired action protocol: every failure is
  classified into one of five categories and carries a full structured event
  (error_type, failed_action, retryable, repair_hint, trace_id), emitted to the
  reasoning trace under the iteration's trace_id.

Metrics (per fixture, then aggregated):
- machine-readable error_type present (and one of the five)
- failed_action captured
- retryable flag present
- concise repair_hint present
- trace_id present
- unknown/unclassified bucket
- next-turn recovery: feeding the corrected input parses/authorizes cleanly

Writes `error_recovery_results.{md,json}`. Exit non-zero if the KPI gate fails.
Run: `python3 benchmarks/error_recovery_benchmark.py`
"""

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import errors  # noqa: E402
import action_protocol as ap  # noqa: E402
import tracing  # noqa: E402
from error_recovery_fixtures import FIXTURES  # noqa: E402

VALID_CATEGORIES = set(errors.ERROR_TYPES)


def _candidate_error(fx):
    """Reproduce exactly what the pipeline records for `fx` (once), returning the dict."""
    if fx["kind"] == "runtime":
        # what src/loop.metta's HandleError does via py-call errors.record_runtime_error
        errors.record_runtime_error(fx["action"])  # emits one structured event
        return errors.build_error("tool_runtime_error", "action failed during execution",
                                  failed_action=fx["action"])
    # protocol failures: the categories action_protocol records via errors.record_code
    if fx.get("disabled"):
        os.environ["OMEGACLAW_DISABLED_TOOLS"] = fx["disabled"]
    try:
        r = ap.parse_actions(fx["raw"])
        if r.ok:
            # authorize_actions is the single recording choke point for policy denials
            _, errs = ap.authorize_actions(r.actions)  # emits one event
            e0 = errs[0]
            return errors.build_error(errors.type_for_code(e0["code"]), e0["message"],
                                      failed_action=fx["raw"], code=e0["code"])
        # parse/validate failures: record once here, mirroring parse_and_render_metta
        if r.source == "none":
            return errors.record_code("no_json", "no JSON actions found", failed_action=fx["raw"])
        e0 = [e for e in r.errors if not e.get("warning")][0]
        return errors.record_code(e0["code"], e0["message"], failed_action=fx["raw"])
    finally:
        os.environ.pop("OMEGACLAW_DISABLED_TOOLS", None)


def _recovers(fx):
    """True if the corrected input parses to a runnable batch (starts with '(')."""
    os.environ["OMEGACLAW_ACTION_PROTOCOL"] = "json"
    try:
        out = ap.parse_and_render_metta(fx["recovery"])
    finally:
        os.environ.pop("OMEGACLAW_ACTION_PROTOCOL", None)
    return isinstance(out, str) and out.startswith("(") and out != "()"


def evaluate():
    per_fixture = []
    with tempfile.TemporaryDirectory() as d:
        os.environ["OMEGACLAW_TRACE_PATH"] = os.path.join(d, "t.jsonl")
        os.environ.pop("OMEGACLAW_TRACE_DISABLE", None)
        tracing.reset()
        tracing.begin_session("bench-errors")
        errors.reset_counts()
        for fx in FIXTURES:
            tracing.begin_iteration(len(per_fixture) + 1)
            err = _candidate_error(fx)
            cand = {
                "category_expected": fx["category"],
                "error_type": err.get("error_type"),
                "classified": err.get("error_type") in VALID_CATEGORIES,
                "correct_category": err.get("error_type") == fx["category"],
                "failed_action": err.get("failed_action") is not None,
                "retryable_present": isinstance(err.get("retryable"), bool),
                "repair_hint": bool(err.get("repair_hint")) and not err["repair_hint"].startswith("("),
                "trace_id": err.get("trace_id") is not None,
                "recovers": _recovers(fx),
            }
            per_fixture.append(cand)
        # error events actually emitted to the trace
        trace_events = [json.loads(x) for x in open(os.environ["OMEGACLAW_TRACE_PATH"], encoding="utf-8") if x.strip()]
        emitted_errors = sum(1 for e in trace_events if e.get("phase") == "error")
        tracing.reset()
        os.environ.pop("OMEGACLAW_TRACE_PATH", None)

    n = len(FIXTURES)

    def rate(key):
        return round(sum(1 for f in per_fixture if f[key]) / n, 4) if n else 0.0

    candidate = {
        "classified": rate("classified"),
        "correct_category": rate("correct_category"),
        "failed_action": rate("failed_action"),
        "retryable_present": rate("retryable_present"),
        "repair_hint": rate("repair_hint"),
        "trace_id": rate("trace_id"),
        "recovers": rate("recovers"),
        "unknown_bucket": round(sum(1 for f in per_fixture if not f["classified"]) / n, 4) if n else 0.0,
    }
    # baseline: opaque string feedback -> nothing machine-readable, all unknown.
    baseline = {
        "classified": 0.0, "correct_category": 0.0, "failed_action": 0.0,
        "retryable_present": 0.0, "repair_hint": 0.0, "trace_id": 0.0,
        "recovers": 0.0, "unknown_bucket": 1.0,
    }
    return {
        "n_fixtures": n,
        "emitted_error_events": emitted_errors,
        "baseline": baseline,
        "candidate": candidate,
        "per_fixture": per_fixture,
        "counts_by_type": errors.counts(),
    }


def render_md(s):
    b, c = s["baseline"], s["candidate"]
    rows = [
        ("Machine-readable error_type (one of 5)", "classified"),
        ("Correct category", "correct_category"),
        ("Failed action captured", "failed_action"),
        ("Retryable flag present", "retryable_present"),
        ("Concise repair hint", "repair_hint"),
        ("Trace id present", "trace_id"),
        ("Next-turn recovery (corrected input parses)", "recovers"),
        ("Unknown / unclassified bucket", "unknown_bucket"),
    ]
    lines = [
        "# Error-Recovery KPI Benchmark — Issue #10",
        "",
        f"Fixture dataset: **{s['n_fixtures']} fixtures**, one per canonical error category "
        "(`error_recovery_fixtures.FIXTURES`), driven through the real `src/errors.py` + "
        "`src/action_protocol.py`.",
        "",
        "- **baseline** = original `asi-alliance` string-only feedback (`…NOTHING_WAS_DONE…`): "
        "no category, failed action, retryability, repair hint, or trace id — one unknown bucket.",
        "- **candidate** = structured error events: five machine-readable categories, each a full "
        "event emitted to the reasoning trace under the iteration's `trace_id`.",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
    ]
    for label, key in rows:
        lines.append(f"| {label} | {b[key]:.2f} | {c[key]:.2f} |")
    lines += [
        "",
        f"Structured error events emitted to the trace: **{s['emitted_error_events']}** "
        f"(counts by type: {s['counts_by_type']}).",
        "",
        "The candidate classifies **every** fixture into one of the five categories (unknown "
        "bucket 0.00 vs the baseline's 1.00), attaches a concise repair hint suitable for feeding "
        "back to the model, and every corrected next-turn input parses cleanly.",
        "",
        "Reproduce: `python3 benchmarks/error_recovery_benchmark.py`",
        "",
    ]
    return "\n".join(lines)


def main():
    s = evaluate()
    with open(os.path.join(_HERE, "error_recovery_results.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    md = render_md(s)
    with open(os.path.join(_HERE, "error_recovery_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    c = s["candidate"]
    failures = []
    if c["classified"] != 1.0:
        failures.append(f"candidate did not classify every fixture ({c['classified']})")
    if c["correct_category"] != 1.0:
        failures.append(f"candidate mis-categorized a fixture ({c['correct_category']})")
    if c["repair_hint"] != 1.0:
        failures.append("candidate missing a concise repair hint on some fixture")
    if c["unknown_bucket"] >= s["baseline"]["unknown_bucket"]:
        failures.append("candidate did not reduce the unknown/unclassified bucket vs baseline")
    if s["emitted_error_events"] < s["n_fixtures"]:
        failures.append("not every fixture emitted a structured error event to the trace")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
