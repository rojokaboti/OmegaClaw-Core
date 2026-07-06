"""KPI benchmark for Issue #6: FreeCiv state-to-atoms & action adapter, baseline vs candidate.

Deterministic and host-runnable (no game server, chromadb, or torch): it exercises the
adapter's normalization/atom-generation/action-validation on the fixture dataset.

* **baseline** = pre-change behavior (what the original repo could do for the same states):
  raw state is passed to the model as text — **no symbolic facts/atoms** are produced and
  **no action legality check** happens before submission, so illegal actions get through.
* **candidate** = this adapter: deterministic `llm_optimized` state -> PLN atoms, plus
  pre-submission `validate_action` that rejects illegal moves.

Metrics:
- determinism: identical state -> byte-identical facts, atoms, and state hash (two runs).
- field coverage: fraction of present convertible state categories turned into >=1 fact.
- invalid-action rate: share of *illegal* candidate actions that would be submitted
  (baseline submits all; candidate must submit none) and legal-action acceptance.

Writes `results.{md,json}` (next to this file). Exit non-zero if the KPI gate fails.
Run: `python3 benchmarks/freeciv/benchmark.py`
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # benchmarks/freeciv
_BENCH = os.path.dirname(_HERE)                             # benchmarks (has the `freeciv` package)
if _BENCH not in sys.path:
    sys.path.insert(0, _BENCH)

from freeciv import adapter, atoms, actions  # noqa: E402
from freeciv.fixtures import FIXTURES  # noqa: E402

COVERAGE_GATE = 0.95


def _atomize(state):
    facts = adapter.facts_from_state(adapter.normalize_state(state))
    return facts, atoms.atoms_from_facts(facts), adapter.state_hash(state)


def _is_deterministic(state):
    """Two independent passes must be byte-identical (facts, atoms, hash)."""
    f1, a1, h1 = _atomize(state)
    f2, a2, h2 = _atomize(state)
    return (json.dumps(f1, sort_keys=True) == json.dumps(f2, sort_keys=True)
            and a1 == a2 and h1 == h2)


def evaluate():
    rows = []
    all_deterministic = True
    cov_sum = 0.0
    base_illegal_submitted = base_illegal_total = 0
    cand_illegal_submitted = cand_illegal_total = 0
    cand_legal_accepted = cand_legal_total = 0
    base_atom_states = 0
    cand_atom_states = 0

    for fx in FIXTURES:
        st = fx["state"]
        facts, atom_list, _ = _atomize(st)
        cov = adapter.coverage(st)["ratio"]
        deterministic = _is_deterministic(st)
        all_deterministic = all_deterministic and deterministic
        cov_sum += cov

        # baseline: no atoms, no validation (raw text -> everything "submitted")
        base_atom_states += 0  # baseline never produces atoms
        base_illegal_total += len(fx["illegal"])
        base_illegal_submitted += len(fx["illegal"])  # no gate -> all illegal actions go through

        # candidate: atoms produced; validate every candidate action
        if atom_list:
            cand_atom_states += 1
        legal_ok = sum(1 for a in fx["legal"] if actions.validate_action(a, st).is_valid)
        illegal_blocked = sum(1 for a in fx["illegal"] if not actions.validate_action(a, st).is_valid)
        cand_legal_accepted += legal_ok
        cand_legal_total += len(fx["legal"])
        cand_illegal_total += len(fx["illegal"])
        cand_illegal_submitted += (len(fx["illegal"]) - illegal_blocked)

        rows.append({
            "id": fx["id"], "category": fx["category"],
            "facts": len(facts), "atoms": len(atom_list),
            "coverage": round(cov, 4), "deterministic": deterministic,
            "legal_accepted": f"{legal_ok}/{len(fx['legal'])}",
            "illegal_blocked": f"{illegal_blocked}/{len(fx['illegal'])}",
        })

    n = len(FIXTURES)
    summary = {
        "n_fixtures": n,
        "deterministic_all": all_deterministic,
        "mean_field_coverage": round(cov_sum / n, 4) if n else 1.0,
        "atomized_states": {"baseline": base_atom_states, "candidate": cand_atom_states, "total": n},
        "invalid_action_rate": {
            "baseline": round(base_illegal_submitted / base_illegal_total, 4) if base_illegal_total else 0.0,
            "candidate": round(cand_illegal_submitted / cand_illegal_total, 4) if cand_illegal_total else 0.0,
        },
        "legal_action_acceptance": {
            "candidate": round(cand_legal_accepted / cand_legal_total, 4) if cand_legal_total else 1.0,
        },
    }
    return rows, summary


def render_md(rows, summary):
    n = summary["n_fixtures"]
    inv = summary["invalid_action_rate"]
    lines = [
        "# FreeCiv State-to-Atoms & Action Adapter KPI Benchmark — Issue #6",
        "",
        f"Fixture dataset: **{n} `llm_optimized` states** (schema-grounded, verified against "
        "freeciv-llm `state_extractor.py`) covering food shortage, undefended city, settler near "
        "threat, tech choice, unit movement, and worker improvement.",
        "",
        "- **baseline** = raw state as text: no symbolic atoms, no pre-submission legality check "
        "(illegal actions reach the game).",
        "- **candidate** = deterministic state->PLN atoms + `validate_action` gate.",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
        f"| States converted to atoms | {summary['atomized_states']['baseline']}/{n} | "
        f"{summary['atomized_states']['candidate']}/{n} |",
        f"| Mean field coverage | 0.00 | {summary['mean_field_coverage']:.2f} |",
        f"| **Invalid-action submission rate** | **{inv['baseline']:.2f}** | **{inv['candidate']:.2f}** |",
        f"| Legal-action acceptance | n/a | {summary['legal_action_acceptance']['candidate']:.2f} |",
        f"| Deterministic facts/atoms (2 runs identical) | n/a | {summary['deterministic_all']} |",
        "",
        "### Per-fixture",
        "",
        "| Fixture | category | facts | atoms | coverage | determ. | legal ok | illegal blocked |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['category']} | {r['facts']} | {r['atoms']} | {r['coverage']:.2f} | "
            f"{r['deterministic']} | {r['legal_accepted']} | {r['illegal_blocked']} |")
    lines += [
        "",
        "The candidate converts every state into deterministic PLN atoms and rejects **100%** of the "
        "illegal candidate actions before they reach `action_submit`, while accepting all legal ones. "
        "The baseline (raw text, no gate) would submit every illegal action. Live win-rate/score KPIs "
        "require a running game and are measured in the live E2E phase (report §5).",
        "",
        "Reproduce: `python3 benchmarks/freeciv/benchmark.py`",
        "",
    ]
    return "\n".join(lines)


def main():
    rows, summary = evaluate()
    results = {"summary": summary, "rows": rows}
    with open(os.path.join(_HERE, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    md = render_md(rows, summary)
    with open(os.path.join(_HERE, "results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    failures = []
    if not summary["deterministic_all"]:
        failures.append("facts/atoms not deterministic across runs")
    if summary["mean_field_coverage"] < COVERAGE_GATE:
        failures.append(f"mean field coverage {summary['mean_field_coverage']} < {COVERAGE_GATE}")
    if summary["invalid_action_rate"]["candidate"] != 0.0:
        failures.append(f"candidate submitted illegal actions (rate {summary['invalid_action_rate']['candidate']})")
    if summary["legal_action_acceptance"]["candidate"] != 1.0:
        failures.append(f"candidate rejected legal actions (acceptance {summary['legal_action_acceptance']['candidate']})")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
