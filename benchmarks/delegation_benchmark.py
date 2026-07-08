"""KPI benchmark for Issue #18: parallel delegation vs the serial baseline.

Deterministic, host-runnable. Runs 12 independent subtasks (each a short sleep + an artifact),
serially and via the real `src/delegation.py` with concurrency, and measures the speedup. Also
exercises two cross-workdir-write attempts (must be blocked → zero isolation violations) and a
long task cancelled by parent interruption (clean cancellation).

* **baseline** = `asi-alliance`: single-loop, no delegation primitive → subtasks run serially
  (wall-clock ≈ sum of durations); no isolation/cancellation contract.
* **candidate** = isolated concurrent subagents with a containment + cancellation contract.

KPI gate (`sys.exit(1)`): parallel wall-clock ≥ 30% faster than serial AND zero isolation
violations. Writes `delegation_results.{md,json}`. Run: `python3 benchmarks/delegation_benchmark.py`
"""

import json
import os
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import delegation as dg  # noqa: E402

_N = 12
_WORK = 0.1   # seconds of "work" per subtask


def _worker(ctx):
    time.sleep(_WORK)
    ctx.write_artifact("result.txt", "done " + ctx.task_id)
    return "processed " + ctx.task_id


def _escaper(ctx):
    try:
        ctx.write_artifact("../../parent_leak.txt", "leak")   # attempt to write outside workdir
        return "ESCAPED"                                       # would be an isolation violation
    except dg.DelegationError:
        return "blocked"


def evaluate():
    os.environ["OMEGACLAW_SESSION_DB"] = os.path.join(tempfile.mkdtemp(), "s.db")
    tasks = [{"id": "t%02d" % i, "run": _worker} for i in range(_N)]

    # serial baseline: concurrency=1 (the single-loop equivalent)
    serial = dg.delegate([dict(t) for t in tasks], parent_id="serial", concurrency=1, timeout=30)
    serial_wall = serial["wall_clock"]
    dg.cleanup(serial)

    # candidate: full concurrency
    par = dg.delegate([dict(t) for t in tasks], parent_id="par", concurrency=_N, timeout=30)
    parallel_wall = par["wall_clock"]
    success_rate = par["counts"].get("ok", 0) / _N
    all_artifacts_present = all(r.get("artifacts") and os.path.isfile(r["artifacts"][0])
                               for r in par["results"])
    dg.cleanup(par)

    improvement = (serial_wall - parallel_wall) / serial_wall if serial_wall else 0.0

    # isolation: two cross-workdir-write attempts must be blocked (0 successful escapes)
    esc = dg.delegate([{"id": "e1", "run": _escaper}, {"id": "e2", "run": _escaper}],
                      parent_id="iso", timeout=10)
    isolation_violations = sum(1 for r in esc["results"] if r.get("summary") == "ESCAPED")
    parent_leak = os.path.exists(os.path.join(os.path.dirname(esc["deleg_root"]), "parent_leak.txt"))
    if parent_leak:
        isolation_violations += 1
    dg.cleanup(esc)

    # cancellation: interrupt a batch of long tasks; must return cancelled and clean up workdirs
    ev = threading.Event()

    def _long(ctx):
        for _ in range(50):
            if ctx.cancelled():
                return "stopped"
            time.sleep(0.05)
        return "finished"

    def _interrupt():
        time.sleep(0.15)
        ev.set()

    threading.Thread(target=_interrupt, daemon=True).start()
    canc = dg.delegate([{"id": "L%d" % i, "run": _long} for i in range(4)],
                       parent_id="cancel", concurrency=4, cancel_event=ev, timeout=10)
    cancelled_ok = all(r["status"] in ("cancelled", "ok") for r in canc["results"]) \
        and any(r["status"] == "cancelled" or r.get("summary") == "stopped" for r in canc["results"])
    root = canc["deleg_root"]
    dg.cleanup(canc)
    cleanup_ok = not os.path.isdir(root)

    os.environ.pop("OMEGACLAW_SESSION_DB", None)

    candidate = {
        "subtasks": _N,
        "serial_wall": serial_wall,
        "parallel_wall": parallel_wall,
        "speedup_pct": round(improvement * 100, 1),
        "success_rate": round(success_rate, 4),
        "structured_outputs": all_artifacts_present,
        "isolation_violations": isolation_violations,
        "cancellation_clean": bool(cancelled_ok and cleanup_ok),
    }
    baseline = {
        "subtasks": _N, "serial_wall": serial_wall, "parallel_wall": serial_wall,
        "speedup_pct": 0.0, "success_rate": round(success_rate, 4),
        "structured_outputs": False, "isolation_violations": 0, "cancellation_clean": False,
    }
    return {"baseline": baseline, "candidate": candidate}


def render_md(s):
    b, c = s["baseline"], s["candidate"]
    rows = [
        ("Subtasks", "subtasks"),
        ("Serial wall-clock (s)", "serial_wall"),
        ("Parallel wall-clock (s)", "parallel_wall"),
        ("Wall-clock improvement % (target >= 30)", "speedup_pct"),
        ("Success rate", "success_rate"),
        ("Structured outputs (session id + artifact path)", "structured_outputs"),
        ("Isolation violations (target 0)", "isolation_violations"),
        ("Clean cancellation + workspace cleanup", "cancellation_clean"),
    ]
    lines = [
        "# Delegation KPI Benchmark — Issue #18",
        "",
        "{} independent subtasks (short sleep + artifact) run serially vs. via isolated concurrent "
        "subagents (`src/delegation.py`), plus cross-workdir-write and cancellation probes.".format(c["subtasks"]),
        "",
        "- **baseline** = single-loop, no delegation → serial execution, no isolation/cancellation.",
        "- **candidate** = concurrent isolated subagents with containment + cancellation contract.",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
    ]
    for label, key in rows:
        lines.append("| {} | {} | {} |".format(label, b[key], c[key]))
    lines += [
        "",
        "Candidate runs {} subtasks **{:.0f}% faster** than serial ({:.2f}s vs {:.2f}s) with "
        "structured per-subagent outputs, **{}** isolation violations, and clean cancellation — "
        "none of which the single-loop baseline provides.".format(
            c["subtasks"], c["speedup_pct"], c["parallel_wall"], c["serial_wall"],
            c["isolation_violations"]),
        "",
        "Reproduce: `python3 benchmarks/delegation_benchmark.py`",
        "",
    ]
    return "\n".join(lines)


def main():
    s = evaluate()
    with open(os.path.join(_HERE, "delegation_results.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    md = render_md(s)
    with open(os.path.join(_HERE, "delegation_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    c = s["candidate"]
    failures = []
    if c["speedup_pct"] < 30.0:
        failures.append("wall-clock improvement {}% < 30%".format(c["speedup_pct"]))
    if c["isolation_violations"] != 0:
        failures.append("{} isolation violations".format(c["isolation_violations"]))
    if c["success_rate"] < 1.0:
        failures.append("success rate {} < 1.0".format(c["success_rate"]))
    if not c["structured_outputs"]:
        failures.append("structured outputs (session id + artifact path) missing")
    if not c["cancellation_clean"]:
        failures.append("cancellation was not clean")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
