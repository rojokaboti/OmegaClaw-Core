"""KPI benchmark for Issue #10: structured error recovery vs the string-only baseline.

Deterministic and host-runnable (no MeTTa/LLM/Docker). It drives the REAL
production paths — `action_protocol.parse_and_render_metta` for parse/validate/
authorize failures and `errors.record_runtime_error` (what `src/loop.metta`'s
HandleError calls via py-call) for runtime failures — over one fixture per error
category (`error_recovery_fixtures.FIXTURES`), and then asserts against the
**actual JSONL event emitted to the durable trace** (not a locally-built dict), so
a regression in the persisted payload is caught here.

* **baseline** = original `asi-alliance/OmegaClaw-Core@main` behavior: an opaque
  string (`…NOTHING_WAS_DONE…` / `ERROR_FEEDBACK: …`) — no category, no failed
  action, no retryability, no repair hint, no trace id. One unknown bucket.
* **candidate** = the emitted `phase=="error"` event carries the full schema:
  `error_type` (category), the original granular `code` (e.g. `missing_arg`),
  `retryable`, `repair_hint`, `failed_action_sha` (always; body under
  `OMEGACLAW_TRACE_BODIES`), all under the iteration's `trace_id`.

Metrics are read off the emitted event. Writes `error_recovery_results.{md,json}`.
Exit non-zero if the KPI gate fails. Run: `python3 benchmarks/error_recovery_benchmark.py`
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


def _emit_production_event(fx, path, bodies=False):
    """Drive the real production path for `fx` into `path`; return the emitted error event."""
    os.environ["OMEGACLAW_TRACE_PATH"] = path
    os.environ.pop("OMEGACLAW_TRACE_DISABLE", None)
    if bodies:
        os.environ["OMEGACLAW_TRACE_BODIES"] = "1"
    else:
        os.environ.pop("OMEGACLAW_TRACE_BODIES", None)
    tracing.reset()
    tracing.begin_session("bench-errors")
    tracing.begin_iteration(1)
    if fx["kind"] == "runtime":
        errors.record_runtime_error(fx["action"])  # exactly what loop.metta py-calls
    else:
        if fx.get("disabled"):
            os.environ["OMEGACLAW_DISABLED_TOOLS"] = fx["disabled"]
        os.environ["OMEGACLAW_ACTION_PROTOCOL"] = "json"
        try:
            ap.parse_and_render_metta(fx["raw"])  # real parse->validate->authorize path
        finally:
            os.environ.pop("OMEGACLAW_DISABLED_TOOLS", None)
            os.environ.pop("OMEGACLAW_ACTION_PROTOCOL", None)
    with open(path, encoding="utf-8") as f:
        events = [json.loads(x) for x in f if x.strip()]
    tracing.reset()
    os.environ.pop("OMEGACLAW_TRACE_PATH", None)
    os.environ.pop("OMEGACLAW_TRACE_BODIES", None)
    errs = [e for e in events if e.get("phase") == "error"]
    return errs, events


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
    total_emitted = 0
    with tempfile.TemporaryDirectory() as d:
        errors.reset_counts()
        for i, fx in enumerate(FIXTURES):
            # default (privacy) mode: measures the durable-by-default payload
            errs, _ = _emit_production_event(fx, os.path.join(d, "fx%d.jsonl" % i), bodies=False)
            total_emitted += len(errs)
            ev = errs[0] if errs else {}
            # bodies mode: prove the redacted failed_action body is recoverable
            errs_b, _ = _emit_production_event(fx, os.path.join(d, "fxb%d.jsonl" % i), bodies=True)
            evb = errs_b[0] if errs_b else {}
            rep = ev.get("repair_hint")
            per_fixture.append({
                "category_expected": fx["category"],
                "error_type": ev.get("error_type"),
                "code": ev.get("code"),
                "emitted": bool(errs),
                "classified": ev.get("error_type") in VALID_CATEGORIES,
                "correct_category": ev.get("error_type") == fx["category"],
                "original_code": bool(ev.get("code")),
                "retryable_present": "retryable" in ev,
                "repair_hint": bool(rep) and not rep.startswith("("),
                "failed_action_ref": ev.get("failed_action_sha") is not None,
                "failed_action_body_recoverable": bool(evb.get("failed_action")),
                "trace_id": ev.get("trace_id") is not None,
                "recovers": _recovers(fx),
            })

    n = len(FIXTURES)

    def rate(key):
        return round(sum(1 for f in per_fixture if f[key]) / n, 4) if n else 0.0

    candidate = {k: rate(k) for k in (
        "classified", "correct_category", "original_code", "retryable_present",
        "repair_hint", "failed_action_ref", "failed_action_body_recoverable",
        "trace_id", "recovers")}
    candidate["unknown_bucket"] = round(
        sum(1 for f in per_fixture if not f["classified"]) / n, 4) if n else 0.0

    baseline = {k: 0.0 for k in candidate}
    baseline["unknown_bucket"] = 1.0

    return {
        "n_fixtures": n,
        "emitted_error_events": total_emitted,
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
        ("Original protocol code preserved (e.g. missing_arg)", "original_code"),
        ("Retryable flag present", "retryable_present"),
        ("Concise repair hint", "repair_hint"),
        ("Failed action ref in trace (sha, privacy-default)", "failed_action_ref"),
        ("Failed action body recoverable (OMEGACLAW_TRACE_BODIES)", "failed_action_body_recoverable"),
        ("Trace id present", "trace_id"),
        ("Next-turn recovery (corrected input parses)", "recovers"),
        ("Unknown / unclassified bucket", "unknown_bucket"),
    ]
    lines = [
        "# Error-Recovery KPI Benchmark — Issue #10",
        "",
        f"Fixture dataset: **{s['n_fixtures']} fixtures**, one per canonical error category "
        "(`error_recovery_fixtures.FIXTURES`), driven through the real production paths "
        "(`action_protocol.parse_and_render_metta` and `errors.record_runtime_error`).",
        "",
        "**Metrics are read off the actual JSONL event emitted to the durable trace** "
        "(`phase==\"error\"`), not a locally-built dict — so a regression in the persisted "
        "payload is caught here.",
        "",
        "- **baseline** = original `asi-alliance` string-only feedback (`…NOTHING_WAS_DONE…`): "
        "no category, code, failed action, retryability, repair hint, or trace id — one unknown bucket.",
        "- **candidate** = structured error events with the full schema under the iteration's `trace_id`.",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
    ]
    for label, key in rows:
        lines.append(f"| {label} | {b[key]:.2f} | {c[key]:.2f} |")
    lines += [
        "",
        f"Structured error events emitted to the trace: **{s['emitted_error_events']}** for "
        f"{s['n_fixtures']} fixtures in default mode (counts by category: {s['counts_by_type']}).",
        "",
        "The emitted event carries the machine-readable **category**, the **original granular code** "
        "(so downstream analytics can recover both the classification and the exact protocol failure), "
        "the **retryable** flag, a concise **repair hint**, and a **failed-action reference** "
        "(`failed_action_sha` always; the redacted body under `OMEGACLAW_TRACE_BODIES`, matching the "
        "prompt/result body-privacy gate). Baseline recovers none of this.",
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
    gate_keys = ("classified", "correct_category", "original_code", "retryable_present",
                 "repair_hint", "failed_action_ref", "failed_action_body_recoverable")
    failures = []
    for k in gate_keys:
        if c[k] != 1.0:
            failures.append(f"candidate {k} = {c[k]} (expected 1.0)")
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
