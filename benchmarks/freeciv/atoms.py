"""Render facts into MeTTa/PLN word-form atoms.

Dialect (locked decision): PLN word-form, matching what ``src/memory_schema.py`` already
stores as ``atoms_json`` (e.g. ``(Inheritance CityA LowFood)``) — NOT NAL arrow-form. Truth
values are always ``(stv frequency confidence)`` in [0,1] (docs/reference-lib-nal.md).

Two renderings per fact:
  - ``statement`` — the bare link, e.g. ``(Inheritance City_1 LowFood)``. This is what goes
    into a ``remember_claim(..., atoms=[...])`` list (chroma stores it as a JSON string).
  - ``sentence``  — statement + truth value, e.g. ``((Inheritance City_1 LowFood) (stv 1.0 0.99))``
    — the form the NAL/PLN engines consume as a premise.

Output is deterministically sorted.
"""

from . import adapter


def _statement(fact):
    subj, pred, obj = fact["subj"], fact["pred"], fact["obj"]
    if pred == "Inheritance":
        return "(Inheritance {} {})".format(subj, obj)
    if pred == "Similarity":
        return "(Similarity {} {})".format(subj, obj)
    # Evaluation: encode obj as "Predicate:arg[:arg...]" -> (Evaluation (Predicate P) (List ...))
    parts = obj.split(":")
    predicate = parts[0]
    args = [subj] + parts[1:]
    return "(Evaluation (Predicate {}) (List {}))".format(predicate, " ".join(args))


def _stv(fact):
    return "(stv {} {})".format(_fmt(fact["f"]), _fmt(fact["c"]))


def _fmt(x):
    # Stable numeric rendering: 1.0 -> "1.0", 0.99 -> "0.99" (no locale/scientific drift).
    x = float(x)
    if x == int(x):
        return "{:.1f}".format(x)
    return ("%.6f" % x).rstrip("0").rstrip(".")


def atoms_from_facts(facts):
    """Return a sorted list of bare PLN statements (for remember_claim atoms=[...])."""
    stmts = sorted({_statement(f) for f in facts})
    return stmts


def sentences_from_facts(facts):
    """Return a sorted list of ``(statement (stv f c))`` premises for the reasoner."""
    seen = {}
    for f in facts:
        stmt = _statement(f)
        # If the same statement appears twice, keep the higher-confidence truth value.
        if stmt not in seen or (f["f"], f["c"]) > seen[stmt]:
            seen[stmt] = (f["f"], f["c"])
    out = []
    for stmt in sorted(seen):
        fq, cf = seen[stmt]
        out.append("({} (stv {} {}))".format(stmt, _fmt(fq), _fmt(cf)))
    return out


def atoms_from_state(raw):
    """Convenience: raw llm_optimized state -> (bare statements, full sentences)."""
    facts = adapter.facts_from_state(adapter.normalize_state(raw))
    return atoms_from_facts(facts), sentences_from_facts(facts)
