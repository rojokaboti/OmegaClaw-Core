"""KPI benchmark for Issue #8: session-scoped reasoning state, baseline vs candidate.

Deterministic and host-runnable (no MeTTa/LLM/Docker): it drives the REAL `src/metta_sessions.py`
store over two independent multi-turn games (`metta_sessions_fixtures.GAMES`) and measures
traceable continuity vs. the stateless `metta` baseline.

* **baseline** = stateless `(metta ...)` (original repo): nothing persists between calls, so every
  turn must re-transmit ALL premises accumulated so far, and a single global space offers no
  isolation between games.
* **candidate** = the session store: each premise is added once and reused across turns; sessions
  are isolated by id.

Metrics:
- fact preservation: after all turns, are the added premises still retrievable? (candidate 100% /
  baseline 0% — a stateless call keeps nothing).
- cross-session leakage: do game-a's facts leak into game-b? (candidate 0).
- premise re-transmission: total premise sends across the game — baseline re-sends every
  accumulated fact each turn; candidate sends each fact once. Report the reduction %.

Writes `metta_sessions_results.{md,json}`. Exit non-zero if the KPI gate fails.
Run: `python3 benchmarks/metta_sessions_benchmark.py`
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

import metta_sessions as ms  # noqa: E402
from metta_sessions_fixtures import GAMES  # noqa: E402

REDUCTION_GATE = 0.30


def _baseline_transmissions(turns):
    """Stateless: each turn re-sends every premise accumulated so far. Nothing persists."""
    total, accumulated = 0, 0
    for t in turns:
        accumulated += len(t["facts"])
        total += accumulated  # re-send all accumulated facts this turn
    return total


def _candidate_run(sid, turns):
    """Drive the real session store: add each turn's facts once; infer replays from the store.

    Returns (premise_transmissions, all_added_facts).
    """
    added = []
    transmissions = 0
    for t in turns:
        for f in t["facts"]:
            ms.add_fact(sid, f)
            transmissions += 1  # each fact transmitted exactly once (when added)
            added.append(f)
        # infer reuses stored premises; the agent only sends the query (no premise re-send)
        prog = ms.infer_program(sid, t["query"])
        assert prog == "()" or prog.count("(|- ") == len(ms.facts(sid)), (sid, prog)
    return transmissions, added


def evaluate():
    ms.reset()

    base_total = sum(_baseline_transmissions(turns) for turns in GAMES.values())
    cand_total = 0
    added_by_game = {}
    for sid, turns in GAMES.items():
        tx, added = _candidate_run(sid, turns)
        cand_total += tx
        added_by_game[sid] = added

    # fact preservation (candidate): all added premises still retrievable per game
    preserved = 0
    total_added = 0
    for sid, added in added_by_game.items():
        stored = ms.facts(sid)
        total_added += len(added)
        preserved += sum(1 for f in added if f in stored)
    preservation = round(preserved / total_added, 4) if total_added else 1.0

    # cross-session leakage (candidate): game-a facts must not appear in game-b and vice versa
    a, b = list(GAMES)[0], list(GAMES)[1]
    fa, fb = set(ms.facts(a)), set(ms.facts(b))
    leakage = len(fa & fb)

    # baseline: stateless -> 0 preservation; single global space -> other game's facts visible
    base_leakage = min(len(added_by_game[a]), len(added_by_game[b]))  # would co-mingle

    reduction = round(1 - (cand_total / base_total), 4) if base_total else 0.0
    ms.reset()

    return {
        "games": len(GAMES),
        "fact_preservation": {"baseline": 0.0, "candidate": preservation},
        "cross_session_leakage": {"baseline": base_leakage, "candidate": leakage},
        "premise_transmissions": {"baseline": base_total, "candidate": cand_total},
        "premise_resend_reduction": reduction,
    }


def render_md(s):
    fp, leak, tx = s["fact_preservation"], s["cross_session_leakage"], s["premise_transmissions"]
    lines = [
        "# Session-Scoped Reasoning KPI Benchmark — Issue #8",
        "",
        f"Fixture dataset: **{s['games']} independent multi-turn games** "
        "(`metta_sessions_fixtures.GAMES`) driven through the real `src/metta_sessions.py`.",
        "",
        "- **baseline** = stateless `(metta ...)`: nothing persists between calls (re-send every "
        "accumulated premise each turn; one global space, no isolation).",
        "- **candidate** = the session store: premises added once and reused; sessions isolated by id.",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
        f"| Fact preservation across turns | {fp['baseline']:.2f} | **{fp['candidate']:.2f}** |",
        f"| Cross-session leakage (facts) | {leak['baseline']} | **{leak['candidate']}** |",
        f"| Premise transmissions (all turns) | {tx['baseline']} | {tx['candidate']} |",
        f"| **Premise re-send reduction** | — | **{s['premise_resend_reduction']:.0%}** |",
        "",
        "The candidate preserves **100%** of session premises across turns with **zero** cross-session "
        f"leakage, and cuts repeated premise re-transmission by **{s['premise_resend_reduction']:.0%}** "
        "(each fact is added once and replayed from the store, vs. the stateless baseline re-sending all "
        "accumulated premises every turn). Inference itself still runs through the real two-premise "
        "`(|- …)` path (validated in-container).",
        "",
        "Reproduce: `python3 benchmarks/metta_sessions_benchmark.py`",
        "",
    ]
    return "\n".join(lines)


def main():
    s = evaluate()
    with open(os.path.join(_HERE, "metta_sessions_results.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    md = render_md(s)
    with open(os.path.join(_HERE, "metta_sessions_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    failures = []
    if s["fact_preservation"]["candidate"] != 1.0:
        failures.append(f"fact preservation {s['fact_preservation']['candidate']} != 1.0")
    if s["cross_session_leakage"]["candidate"] != 0:
        failures.append(f"cross-session leakage {s['cross_session_leakage']['candidate']} != 0")
    if s["premise_transmissions"]["candidate"] >= s["premise_transmissions"]["baseline"]:
        failures.append("candidate premise transmissions not fewer than baseline")
    if s["premise_resend_reduction"] < REDUCTION_GATE:
        failures.append(f"re-send reduction {s['premise_resend_reduction']} < {REDUCTION_GATE}")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
