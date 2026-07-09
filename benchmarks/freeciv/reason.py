"""Authentic MeTTa/PLN reasoning bridge for the A/B experiment.

`derive(fact_sentences)` runs OmegaClaw's REAL PLN engine (lib_pln, via the PeTTa interpreter) over
the turn's observed fact atoms + the FreeCiv rule set (`benchmarks/freeciv/rules.metta`) and returns
the derived recommendation atoms. Each `(|- fact rule)` fires lib_pln's Modus Ponens, so an observed
`((Inheritance City_1 Undefended) (stv 1.0 0.99))` + the Undefended rule derives
`((Recommend City_1 Defend) (stv ...))`.

This only works inside the omegaclaw container (PeTTa/hyperon is not on the host). On a host without
the interpreter, `derive` returns `[]` (best-effort) so importing this module never breaks anything.

Env overrides (finalized during the in-container spike):
- OMEGACLAW_METTA_CMD     interpreter command (default ``sh /PeTTa/run.sh``); the program file is
                          appended as the final arg.
- OMEGACLAW_METTA_CWD     working dir for the interpreter (default ``/PeTTa``).
- OMEGACLAW_REASON_IMPORTS override the import preamble (newline-separated MeTTa import lines).
- OMEGACLAW_REASON_DEBUG  truthy -> print the generated program + raw interpreter output to stderr.
"""

import os
import re
import shlex
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))            # benchmarks/freeciv
_REPO = os.path.dirname(os.path.dirname(_HERE))               # repo root

# Import preamble: replicate run.metta's setup so the `OmegaClaw-Core` library root is registered
# (lib_import provides `library`/`git-import!`; git-import! resolves to the LOCAL repos/ clone
# offline). Then pull in the reasoning libs + our rules. PLN word-form inference uses `|~`
# (lib_pln), not `|-` (arrow-form NAL) — see rules.metta.
_DEFAULT_IMPORTS = "\n".join([
    "!(import! &self (library lib_import))",
    '!(git-import! "https://github.com/asi-alliance/OmegaClaw-Core.git")',
    "!(import! &self (library OmegaClaw-Core lib_nal))",
    "!(import! &self (library OmegaClaw-Core lib_pln))",
    "!(import! &self (library OmegaClaw-Core ./benchmarks/freeciv/rules))",
])

# A derived recommendation atom: (Recommend <entity> <action>) [optionally followed by an stv].
_REC_RE = re.compile(r"\(Recommend\s+([^\s()]+)\s+([^\s()]+)\)")


def _debug():
    return (os.environ.get("OMEGACLAW_REASON_DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}


def _imports():
    return os.environ.get("OMEGACLAW_REASON_IMPORTS") or _DEFAULT_IMPORTS


def build_program(fact_sentences):
    """Build the MeTTa program: imports + one (recommend-for <fact>) per observed fact."""
    lines = [_imports(), ""]
    for f in fact_sentences:
        lines.append("!(recommend-for {})".format(f))
    return "\n".join(lines) + "\n"


def _eval(program, timeout):
    """Run the PeTTa interpreter on `program`; return stdout (raises on failure)."""
    cmd = shlex.split(os.environ.get("OMEGACLAW_METTA_CMD", "sh /PeTTa/run.sh"))
    cwd = os.environ.get("OMEGACLAW_METTA_CWD", "/PeTTa")
    fd, path = tempfile.mkstemp(suffix=".metta", prefix="fc_reason_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(program)
        proc = subprocess.run(cmd + [path], cwd=cwd, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if _debug():
            sys.stderr.write("=== reason program ===\n{}\n=== output ===\n{}\n".format(program, out))
        return out
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def derive(fact_sentences, timeout=30):
    """Return unique derived recommendation atoms for the given fact sentences (``[]`` on any failure)."""
    facts = [f.strip() for f in (fact_sentences or []) if f and f.strip()]
    if not facts:
        return []
    try:
        out = _eval(build_program(facts), timeout)
    except Exception:  # noqa: BLE001 - reasoning is best-effort; never break the arm
        return []
    seen, recs = set(), []
    for m in _REC_RE.finditer(out):
        entity, action = m.group(1), m.group(2)
        # Skip ungrounded rule templates that leak from non-matching fact/rule pairings
        # (e.g. "(Recommend $c Defend)"): a real recommendation has a concrete entity.
        if entity.startswith("$") or action.startswith("$"):
            continue
        key = (entity, action)
        if key not in seen:
            seen.add(key)
            recs.append("(Recommend {} {})".format(entity, action))
    return recs


def format_for_llm(recommendations):
    """Render derived recommendations as a concise prompt block (empty string if none).

    Framed as OPTIONAL hints, NOT a checklist. The 2026-07-08 head-to-head duel showed the LLM
    treated a "recommended priorities this turn" list as exhaustive: it did exactly
    len(recommendations) actions and stopped (proposed == n_conclusions on 97-99% of turns, ~1.6-2.1
    actions/turn vs the plain arm's ~2.9), a compounding activity deficit that starved expansion.
    The wording below decouples the action budget from the recommendation count.
    """
    if not recommendations:
        return ""
    lines = ["DERIVED (PLN reasoning) — optional strategic hints (NOT a to-do list):"]
    for r in recommendations:
        m = _REC_RE.match(r)
        if m:
            lines.append("  - {} → {}".format(m.group(1), m.group(2)))
    lines.append(
        "(Hints only — do NOT limit yourself to these. Still choose the FULL 1-3 actions using your "
        "own judgment, including expansion such as founding new cities with settlers, which the "
        "hints may omit.)")
    return "\n".join(lines)


if __name__ == "__main__":
    # Spike helper: derive from a captured state file, or from stdin fact lines.
    import json
    sys.path.insert(0, os.path.dirname(_HERE))  # benchmarks (for the `freeciv` package)
    from freeciv import adapter, atoms  # noqa: E402
    if len(sys.argv) > 1:
        state = json.load(open(sys.argv[1], encoding="utf-8"))
        facts = atoms.sentences_from_facts(adapter.facts_from_state(adapter.normalize_state(state)))
    else:
        facts = [ln.strip() for ln in sys.stdin if ln.strip()]
    print("input facts: {}".format(len(facts)))
    recs = derive(facts)
    print("derived {} recommendation(s):".format(len(recs)))
    for r in recs:
        print("  ", r)
    print("\n" + (format_for_llm(recs) or "(none)"))
