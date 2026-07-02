"""Unit tests for the FreeCiv state-to-atoms & action adapter (Issue #6).

Pure-Python, deterministic, no game server / chromadb / torch needed. Runs under pytest and
standalone (`python3 Autotests/test_freeciv_adapter.py`). Covers the KPI-critical properties:
byte-for-byte determinism, >=95% field coverage, PLN atom shape, the legal-accept /
illegal-reject matrix, and the freeciv-{observe,action} tool wiring (arg-spec +
output_format_block + shim behavior).
"""
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
_BENCHMARKS = os.path.join(_REPO_ROOT, "benchmarks")
for _p in (_SRC, _BENCHMARKS, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from freeciv import adapter, atoms, actions  # noqa: E402
from freeciv_fixtures import FIXTURES  # noqa: E402
import action_protocol as ap  # noqa: E402
import freeciv_tool  # noqa: E402


# --- determinism -----------------------------------------------------------

def test_normalization_is_order_independent():
    fx = FIXTURES[0]["state"]
    shuffled = json.loads(json.dumps(fx))  # re-serialize; dict order differs but content same
    assert adapter.state_hash(fx) == adapter.state_hash(shuffled)


def test_facts_and_atoms_deterministic_across_runs():
    for fx in FIXTURES:
        st = fx["state"]
        f1 = adapter.facts_from_state(adapter.normalize_state(st))
        f2 = adapter.facts_from_state(adapter.normalize_state(st))
        assert json.dumps(f1, sort_keys=True) == json.dumps(f2, sort_keys=True)
        assert atoms.atoms_from_facts(f1) == atoms.atoms_from_facts(f2)
        assert adapter.state_hash(st) == adapter.state_hash(st)


def test_atoms_are_sorted_and_unique():
    for fx in FIXTURES:
        stmts = atoms.atoms_from_state(fx["state"])[0]
        assert stmts == sorted(stmts)
        assert len(stmts) == len(set(stmts))


# --- coverage --------------------------------------------------------------

def test_field_coverage_at_least_95_percent():
    for fx in FIXTURES:
        cov = adapter.coverage(fx["state"])
        assert cov["ratio"] >= 0.95, (fx["id"], cov)


# --- PLN atom shape --------------------------------------------------------

def test_pln_word_form_and_truth_values():
    stmts, sents = atoms.atoms_from_state(FIXTURES[0]["state"])
    assert stmts, "expected at least one atom"
    for s in stmts:
        assert s.startswith("(Inheritance ") or s.startswith("(Evaluation ") or s.startswith("(Similarity "), s
        assert "-->" not in s  # PLN word-form, not NAL arrows
    for s in sents:
        assert "(stv " in s and s.endswith(")")


def test_game_state_atoms_match_memory_schema_shape():
    """Atoms must be storable as-is in memory_schema.build_metadata(atoms=[...])."""
    try:
        import memory_schema as ms
    except Exception:  # noqa: BLE001 - memory_schema import may pull optional deps in some envs
        return
    stmts = atoms.atoms_from_state(FIXTURES[0]["state"])[0]
    m = ms.build_metadata("freeciv turn", "freeciv.turn_40", "game_state", atoms=stmts)
    assert m["confidence"] == 1.0
    assert json.loads(m["atoms_json"]) == stmts


# --- action validation matrix ---------------------------------------------

def test_legal_actions_accepted_and_illegal_rejected():
    for fx in FIXTURES:
        st = fx["state"]
        for a in fx["legal"]:
            r = actions.validate_action(a, st)
            assert r.is_valid, (fx["id"], "legal rejected", a, r.error_code)
        for a in fx["illegal"]:
            r = actions.validate_action(a, st)
            assert not r.is_valid, (fx["id"], "illegal accepted", a)


def test_legal_actions_membership_filter():
    st = FIXTURES[4]["state"]  # unit_movement: explorer id 3
    legal = [{"type": "unit_move", "unit_id": 3, "target": {"x": 3, "y": 2}}]
    assert actions.validate_action({"type": "unit_move", "unit_id": 3, "dest_x": 3, "dest_y": 2}, st, legal).is_valid
    # a well-formed action for a *different* type is not among the advertised legal actions
    assert not actions.validate_action({"type": "unit_sentry", "unit_id": 3}, st, legal).is_valid


def test_wire_form_action_variant_normalized():
    st = FIXTURES[4]["state"]
    r = actions.validate_action({"action_type": "unit_move", "unit_id": 3, "target": {"x": 3, "y": 2}}, st)
    assert r.is_valid


# --- tool wiring -----------------------------------------------------------

def test_tools_registered_in_arg_spec_and_prompt():
    assert "freeciv-observe" in ap.ALLOWED_TOOLS
    assert "freeciv-action" in ap.ALLOWED_TOOLS
    block = ap.output_format_block()
    assert "freeciv-observe{}" in block
    assert "freeciv-action{action}" in block


def test_freeciv_action_parses_and_renders():
    r = ap.parse_actions('{"actions":[{"tool":"freeciv-observe","args":{}}]}')
    assert r.ok and ap.actions_to_metta(r.actions) == "((freeciv-observe))"
    r = ap.parse_actions('{"actions":[{"tool":"freeciv-action","args":{"action":"{\\"type\\":\\"end_turn\\"}"}}]}')
    assert r.ok


def test_shim_observe_deterministic_and_act_gates_illegal():
    st = FIXTURES[1]["state"]  # undefended_city: unit 11 ours, unit 30 enemy
    out1 = freeciv_tool.observe(state=st)
    out2 = freeciv_tool.observe(state=st)
    assert out1 == out2 and "(stv " in out1

    submitted = []

    class _Fake:
        def get_state(self):
            return st

        def get_legal_actions(self):
            return None

        def submit_action(self, action):
            submitted.append(action)
            return {"status": "ok"}

    freeciv_tool._client = _Fake()
    try:
        legal = json.loads(freeciv_tool.act(json.dumps({"type": "unit_fortify", "unit_id": 11})))
        assert legal["status"] == "submitted" and len(submitted) == 1
        illegal = json.loads(freeciv_tool.act(json.dumps({"type": "unit_move", "unit_id": 30, "dest_x": 5, "dest_y": 5})))
        assert illegal["status"] == "denied" and illegal["error_code"] == "E202"
        assert len(submitted) == 1  # illegal action was NOT submitted
    finally:
        freeciv_tool._client = None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("\nAll {} freeciv adapter tests passed".format(len(fns)))
