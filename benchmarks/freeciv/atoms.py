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


# --------------------------------------------------------------------------- validation

# The link constructors the NAL/PLN reasoner accepts in word-form (see lib_pln.metta).
_KNOWN_LINKS = ("Inheritance", "Similarity", "Implication", "Equivalence", "Member", "Evaluation")


def _parse_sexpr(s):
    """Minimal S-expression parser: returns a nested list/str tree, or raises ValueError.

    This is the same shape MeTTa's ``sread`` expects, so a string that parses here is a
    syntactically valid atom the AtomSpace can ingest. Our tokens never contain spaces
    (see adapter._tok), so whitespace tokenization is safe.
    """
    toks = s.replace("(", " ( ").replace(")", " ) ").split()
    if not toks:
        raise ValueError("empty atom")
    pos = 0

    def parse():
        nonlocal pos
        if pos >= len(toks):
            raise ValueError("unexpected end of atom")
        t = toks[pos]
        pos += 1
        if t == "(":
            node = []
            while pos < len(toks) and toks[pos] != ")":
                node.append(parse())
            if pos >= len(toks):
                raise ValueError("unbalanced '(' (missing ')')")
            pos += 1  # consume ')'
            return node
        if t == ")":
            raise ValueError("unexpected ')'")
        return t

    tree = parse()
    if pos != len(toks):
        raise ValueError("trailing tokens after atom")
    return tree


def validate_atom(statement):
    """Return None if `statement` is a well-formed PLN atom, else a reason string."""
    try:
        tree = _parse_sexpr(statement)
    except ValueError as e:
        return "unparseable: {}".format(e)
    if not isinstance(tree, list) or not tree or not isinstance(tree[0], str):
        return "atom is not a (Head ...) expression"
    head = tree[0]
    if head not in _KNOWN_LINKS:
        return "unknown link constructor {!r}".format(head)
    if head == "Evaluation":
        if len(tree) != 3 or not (isinstance(tree[1], list) and tree[1][:1] == ["Predicate"]):
            return "Evaluation must be (Evaluation (Predicate P) (List ...))"
        if not (isinstance(tree[2], list) and tree[2][:1] == ["List"] and len(tree[2]) >= 2):
            return "Evaluation missing non-empty (List ...)"
    else:  # binary links: (Head A B)
        if len(tree) != 3 or not all(isinstance(x, (str, list)) for x in tree[1:]):
            return "{} must be ({} A B)".format(head, head)
    return None


def _valid_truth(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return 0.0 <= v <= 1.0


def validate_sentence(sentence):
    """Return None if `sentence` is ``(<atom> (stv f c))`` with f,c in [0,1], else a reason."""
    try:
        tree = _parse_sexpr(sentence)
    except ValueError as e:
        return "unparseable: {}".format(e)
    if not (isinstance(tree, list) and len(tree) == 2 and isinstance(tree[1], list)):
        return "sentence must be (<atom> (stv f c))"
    stv = tree[1]
    if stv[:1] != ["stv"] or len(stv) != 3 or not (_valid_truth(stv[1]) and _valid_truth(stv[2])):
        return "truth value must be (stv f c) with f,c in [0,1]"
    # re-serialize the statement subtree and validate it as an atom
    return validate_atom(_unparse(tree[0]))


def _unparse(tree):
    if isinstance(tree, list):
        return "(" + " ".join(_unparse(t) for t in tree) + ")"
    return str(tree)


def validate_atoms(statements):
    """Return a list of ``(atom, reason)`` for any malformed atoms (empty list == all valid)."""
    return [(a, r) for a in statements for r in [validate_atom(a)] if r]


def assert_well_formed(statements, sentences):
    """Raise ValueError if any atom/sentence is malformed. Use before loading a space."""
    problems = validate_atoms(statements)
    problems += [(s, r) for s in sentences for r in [validate_sentence(s)] if r]
    if problems:
        raise ValueError("malformed atoms: " + "; ".join("{} -> {}".format(a, r) for a, r in problems))
