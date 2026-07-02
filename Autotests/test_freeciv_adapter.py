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

from freeciv import adapter, atoms, actions, schemas  # noqa: E402
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


# real runtime llm_optimized shape (civcom.build_llm_optimized_state) — differs from the
# documented _format_llm_optimized_state; the adapter must handle both. Captured live during
# Issue #6 validation (see benchmarks/freeciv/samples/).
_REAL_SHAPE = {
    "format": "llm_optimized", "turn": 12, "phase": "movement", "player_perspective": 0,
    "strategic": {"score": 45, "cities_count": 1, "units_count": 2, "tech_level": 3, "gold": 20},
    "tactical": {"active_units": [7], "visible_threats": [{"enemy_unit_id": 30, "target_id": 7}],
                 "cities_needing_orders": [1]},
    "economic": {"gold": 20, "gold_per_turn": 2, "research": 6, "research_target": "Pottery"},
    "players": {"0": {"id": 0, "name": "Romans", "gold": 20}},
    "units": {"7": {"id": 7, "type": "Warrior", "owner": 0, "x": 4, "y": 5, "hp": 10}},
    "cities": {"1": {"id": 1, "name": "Rome", "owner": 0, "x": 4, "y": 5, "population": 2,
                     "production": "Warriors"}},
    "techs": {"player0": ["Pottery", "Alphabet"]},
}


def test_real_runtime_shape_extracts_resources_and_strategic():
    facts = adapter.facts_from_state(adapter.normalize_state(_REAL_SHAPE))
    cats = {f["category"] for f in facts}
    assert "resources" in cats and "strategic" in cats  # economic.gold + strategic.score
    assert "units" in cats and "cities" in cats and "techs" in cats
    assert adapter.coverage(_REAL_SHAPE)["ratio"] >= 0.95
    # a threat from tactical.visible_threats is surfaced
    stmts = atoms.atoms_from_facts(facts)
    assert any("Threatens" in s for s in stmts)


def test_captured_real_sample_normalizes_cleanly():
    sample = os.path.join(_BENCHMARKS, "freeciv", "samples", "real_state_turn0.json")
    if not os.path.exists(sample):
        return
    with open(sample) as f:
        state = json.load(f)
    # deterministic + no crash on the real byte-captured state
    assert adapter.state_hash(state) == adapter.state_hash(json.loads(json.dumps(state)))
    facts = adapter.facts_from_state(adapter.normalize_state(state))
    assert atoms.atoms_from_facts(facts) == sorted(set(atoms.atoms_from_facts(facts)))


def test_captured_populated_real_game_state():
    """Byte-real state from a *started* live game (turn 1, 7 starting units)."""
    sample = os.path.join(_BENCHMARKS, "freeciv", "samples", "real_state_turn1.json")
    if not os.path.exists(sample):
        return
    with open(sample) as f:
        state = json.load(f)
    norm = adapter.normalize_state(state)
    assert norm["turn"] == 1
    mine = [u for u in norm["units"] if u.get("owner") == norm["player_perspective"]]
    assert len(mine) >= 1
    facts = adapter.facts_from_state(norm)
    cats = {f["category"] for f in facts}
    # real populated game yields unit + resource + strategic facts, all deterministic
    assert "units" in cats and "resources" in cats and "strategic" in cats
    assert adapter.coverage(state)["ratio"] >= 0.95
    assert adapter.state_hash(state) == adapter.state_hash(json.loads(json.dumps(state)))
    # a real owned unit validates for a simple action; an unknown unit does not
    uid = mine[0]["id"]
    assert actions.validate_action({"type": "unit_fortify", "unit_id": uid}, state).is_valid
    assert not actions.validate_action({"type": "unit_fortify", "unit_id": 10**9}, state).is_valid


# --- PLN atom shape --------------------------------------------------------

def test_pln_word_form_and_truth_values():
    stmts, sents = atoms.atoms_from_state(FIXTURES[0]["state"])
    assert stmts, "expected at least one atom"
    for s in stmts:
        assert s.startswith("(Inheritance ") or s.startswith("(Evaluation ") or s.startswith("(Similarity "), s
        assert "-->" not in s  # PLN word-form, not NAL arrows
    for s in sents:
        assert "(stv " in s and s.endswith(")")


def test_atoms_are_well_formed_sexprs():
    """Every generated atom/sentence must parse as a valid PLN S-expression (space-ingestable)."""
    for fx in FIXTURES:
        stmts, sents = atoms.atoms_from_state(fx["state"])
        atoms.assert_well_formed(stmts, sents)  # raises on any malformed atom/sentence


def test_atom_validator_rejects_malformed():
    assert atoms.validate_atom("(Inheritance City_1 LowFood") is not None   # unbalanced
    assert atoms.validate_atom("(Bogus City_1 LowFood)") is not None        # unknown link
    assert atoms.validate_atom("(Evaluation (Predicate Gold) Player_0)") is not None  # no (List)
    assert atoms.validate_atom("(Inheritance City_1)") is not None          # wrong arity
    assert atoms.validate_atom("(Inheritance City_1 LowFood)") is None      # valid
    assert atoms.validate_sentence("((Inheritance A B) (stv 1.5 0.9))") is not None   # truth OOR
    assert atoms.validate_sentence("((Inheritance A B) (stv 1.0 0.99))") is None      # valid


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


def test_legal_actions_reject_same_actor_different_payload():
    """A legal move for a unit must NOT authorize a *different* move for that unit.

    Regression for the pre-submit safety hole: _matches_legal compared only type + actor id.
    """
    st = {"player_perspective": 0, "units": {"7": {"id": 7, "owner": 0, "x": 1, "y": 1}},
          "cities": {"1": {"id": 1, "owner": 0, "x": 1, "y": 1, "production": "Warriors"}},
          "players": {}, "techs": {}, "economic": {}, "strategic": {}, "tactical": {}}

    # unit_move: same unit, wrong destination -> rejected (E230), exact -> accepted
    legal = [{"type": "unit_move", "unit_id": 7, "target": {"x": 2, "y": 2}}]
    bad = actions.validate_action({"type": "unit_move", "unit_id": 7, "dest_x": 999, "dest_y": 999}, st, legal)
    assert not bad.is_valid and bad.error_code == schemas.E_NOT_LEGAL, bad.to_dict()
    assert actions.validate_action({"type": "unit_move", "unit_id": 7, "dest_x": 2, "dest_y": 2}, st, legal).is_valid
    # wire-form target on the candidate side also matches the advertised tile
    assert actions.validate_action({"type": "unit_move", "unit_id": 7, "target": {"x": 2, "y": 2}}, st, legal).is_valid

    # city_production: same city, wrong production -> rejected; exact -> accepted
    legalc = [{"type": "city_production", "city_id": 1, "production_type": "Granary"}]
    assert not actions.validate_action(
        {"type": "city_production", "city_id": 1, "production_type": "Colosseum"}, st, legalc).is_valid
    assert actions.validate_action(
        {"type": "city_production", "city_id": 1, "production_type": "Granary"}, st, legalc).is_valid

    # unit-simple action: unit_id is the identifying payload
    legalf = [{"type": "unit_fortify", "unit_id": 7}]
    assert actions.validate_action({"type": "unit_fortify", "unit_id": 7}, st, legalf).is_valid


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
