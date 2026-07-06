"""KPI benchmark for Issue #25: turn advancement, baseline vs candidate end_turn envelope.

Deterministic and host-runnable (no Docker/LLM). Drives the real `benchmarks/freeciv/turncycle`
loop against `MockProxyWS`, which replicates the freeciv-proxy's exact action-extraction rule, and
measures how many turns advance in K attempts:

* **baseline** = the pre-#25 client shape `{"type":"action","action_type":"end_turn"}` (top-level
  action_type). The proxy's `message_validator` requires a top-level `action` dict, so this is
  rejected with E220 → no PACKET_PLAYER_PHASE_DONE → the turn never advances (stuck on turn 1).
* **candidate** = `client.end_turn_message()` → `{"type":"action","action":{"action_type":"end_turn"}}`,
  which passes validation, is normalized, and converts to pid 52 → the turn advances every attempt.

Writes `freeciv_turn_cycle_results.{md,json}`. Exit non-zero if the KPI gate fails.
Run: `python3 benchmarks/freeciv_turn_cycle_benchmark.py`
"""

import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from freeciv_turn_cycle_fixtures import baseline_end_turn, candidate_end_turn, drive_turns  # noqa: E402

ATTEMPTS = 5


def evaluate():
    base = asyncio.new_event_loop().run_until_complete(drive_turns(baseline_end_turn(), ATTEMPTS))
    cand = asyncio.new_event_loop().run_until_complete(drive_turns(candidate_end_turn(), ATTEMPTS))
    return {
        "attempts": ATTEMPTS,
        "baseline": {
            "shape": baseline_end_turn(),
            "turns_advanced": len(base),
            "turns_seen": base,
            "monotonic": base == sorted(set(base)) and len(set(base)) == len(base),
            "reached_turn": (base[-1] if base else 1),
        },
        "candidate": {
            "shape": candidate_end_turn(),
            "turns_advanced": len(cand),
            "turns_seen": cand,
            "monotonic": cand == sorted(set(cand)) and len(set(cand)) == len(cand),
            "reached_turn": (cand[-1] if cand else 1),
        },
    }


def render_md(s):
    b, c = s["baseline"], s["candidate"]
    # Render the envelope shapes from the ACTUAL benchmarked payloads (not hardcoded) so the
    # report can never drift from what the code sends.
    b_shape = json.dumps(b["shape"], separators=(",", ":"))
    c_shape = json.dumps(c["shape"], separators=(",", ":"))
    return "\n".join([
        "# FreeCiv Turn-Cycle KPI Benchmark — Issue #25",
        "",
        f"Attempts: **{s['attempts']}** end_turn sends against `MockProxyWS` (models the proxy's "
        "`message_validator` action-required gate + the extract/normalize rule).",
        "",
        f"- **baseline** = pre-#25 client shape `{b_shape}` "
        "(top-level `action_type` — rejected by `message_validator` with `E220`, no pid 52).",
        f"- **candidate** = `{c_shape}` "
        "(`client.end_turn_message()`) — nested under `action`, normalized to `PACKET_PLAYER_PHASE_DONE`.",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
        f"| Turns advanced (of {s['attempts']}) | {b['turns_advanced']} | {c['turns_advanced']} |",
        f"| Reached turn (from 1) | {b['reached_turn']} | {c['reached_turn']} |",
        f"| Monotonically increasing | {b['monotonic'] and b['turns_advanced'] > 0} | {c['monotonic']} |",
        f"| Turns observed | {b['turns_seen'] or '(none)'} | {c['turns_seen']} |",
        "",
        "The candidate advances the turn on **every** attempt (1→2→3→…); the baseline stays stuck on "
        "turn 1 (0 advances), reproducing the Issue #25 symptom and proving the envelope is the cause.",
        "",
        "Reproduce: `python3 benchmarks/freeciv_turn_cycle_benchmark.py`",
        "",
    ])


def main():
    s = evaluate()
    with open(os.path.join(_HERE, "freeciv_turn_cycle_results.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    md = render_md(s)
    with open(os.path.join(_HERE, "freeciv_turn_cycle_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    c, b = s["candidate"], s["baseline"]
    failures = []
    if c["turns_advanced"] < s["attempts"]:
        failures.append(f"candidate advanced only {c['turns_advanced']}/{s['attempts']} turns")
    if not c["monotonic"]:
        failures.append("candidate turns not monotonically increasing")
    if b["turns_advanced"] != 0:
        failures.append(f"baseline unexpectedly advanced {b['turns_advanced']} turns (mock drift?)")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
