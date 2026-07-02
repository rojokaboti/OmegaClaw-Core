"""Deterministic FreeCiv benchmark fixtures — Issue #6.

Each fixture is an `llm_optimized` state (shape verified against
``freeciv-proxy/state_extractor.py``: collections are dicts keyed by string id; units carry
id/type/owner/x/y/hp; cities id/name/owner/population/production; the strategic/tactical/
economic summary blocks) plus candidate actions partitioned into `legal` and `illegal`.

These are **schema-grounded synthetic** states covering the decision types named in the
issue. During the live phase (Phase 0) they are cross-checked against — and where useful
replaced by — byte-real JSON captured from a running freeciv-llm game. The adapter is
tolerant of extra/missing fields, so the byte-real captures drop in without code changes.

`illegal` actions are legality violations the pre-submit validator MUST reject (wrong owner,
unknown entity, off-map target, unknown action type, missing required field) — NOT malformed
JSON. Every `legal` action MUST validate; every `illegal` action MUST be rejected.
"""


def _state(**kw):
    base = {"format": "llm_optimized", "phase": "movement"}
    base.update(kw)
    return base


FIXTURES = [
    {
        "id": "city_food_shortage",
        "category": "economy",
        "state": _state(
            turn=40, player_perspective=1,
            strategic={"victory_progress": {"current_score": 90, "rank": 1, "total_players": 2},
                       "tech_position": {"researched": ["Pottery"], "research_points": 5},
                       "relative_strength": "average"},
            tactical={"unit_groups": {"Settler": {"count": 1, "positions": [[3, 4]], "avg_hp": 10}},
                      "immediate_threats": []},
            economic={"cities": {"count": 1}, "resources": {"gold": 30, "science": 5}},
            players={"1": {"id": 1, "name": "Rome", "gold": 30, "science": 5}},
            units={"5": {"id": 5, "type": "Settler", "owner": 1, "x": 3, "y": 4, "hp": 10}},
            cities={"1": {"id": 1, "name": "Rome", "owner": 1, "x": 3, "y": 4,
                          "population": 2, "production": "Warriors", "food_surplus": -2}},
            techs={"player1": ["Pottery"]},
        ),
        "legal": [
            {"type": "city_production", "city_id": 1, "production_type": "Granary"},
            {"type": "end_turn"},
        ],
        "illegal": [
            {"type": "city_production", "city_id": 2, "production_type": "Granary"},   # unknown city
            {"type": "city_production", "city_id": 1},                                  # missing production_type
        ],
    },
    {
        "id": "undefended_city",
        "category": "military",
        "state": _state(
            turn=22, player_perspective=1,
            strategic={"relative_strength": "weak",
                       "victory_progress": {"current_score": 40, "rank": 2, "total_players": 2}},
            tactical={"unit_groups": {"Warrior": {"count": 1, "positions": [[9, 9]], "avg_hp": 8}},
                      "immediate_threats": [{"enemy_unit_id": 30, "target_id": 2}]},
            economic={"cities": {"count": 1}, "resources": {"gold": 12, "science": 3}},
            players={"1": {"id": 1, "name": "Rome"}, "0": {"id": 0, "name": "Barbarians"}},
            units={"11": {"id": 11, "type": "Warrior", "owner": 1, "x": 9, "y": 9, "hp": 8},
                   "30": {"id": 30, "type": "Legion", "owner": 0, "x": 6, "y": 5, "hp": 10}},
            cities={"2": {"id": 2, "name": "Ostia", "owner": 1, "x": 5, "y": 5, "population": 3,
                          "production": "Phalanx"}},
            techs={"player1": []},
        ),
        "legal": [
            {"type": "unit_move", "unit_id": 11, "dest_x": 5, "dest_y": 5},   # rush the warrior home
            {"type": "unit_fortify", "unit_id": 11},
        ],
        "illegal": [
            {"type": "unit_move", "unit_id": 30, "dest_x": 5, "dest_y": 5},   # move ENEMY legion
            {"type": "unit_fortify", "unit_id": 77},                          # unknown unit
        ],
    },
    {
        "id": "settler_near_threat",
        "category": "military",
        "state": _state(
            turn=15, player_perspective=2,
            strategic={"relative_strength": "average"},
            tactical={"unit_groups": {"Settler": {"count": 1, "positions": [[12, 8]], "avg_hp": 10}},
                      "immediate_threats": [{"enemy_unit_id": 40, "target_id": 21}]},
            economic={"resources": {"gold": 20, "science": 4}},
            players={"2": {"id": 2, "name": "Egypt"}, "0": {"id": 0, "name": "Barbarians"}},
            units={"21": {"id": 21, "type": "Settler", "owner": 2, "x": 12, "y": 8, "hp": 10},
                   "40": {"id": 40, "type": "Horsemen", "owner": 0, "x": 13, "y": 8, "hp": 10}},
            cities={},
            techs={"player2": ["Bronze Working"]},
        ),
        "legal": [
            {"type": "unit_move", "unit_id": 21, "dest_x": 11, "dest_y": 8},  # retreat settler
            {"type": "unit_build_city", "unit_id": 21},                       # settle now
        ],
        "illegal": [
            {"type": "unit_move", "unit_id": 21, "dest_x": -3, "dest_y": 8},   # off-map
            {"type": "unit_build_city", "unit_id": 40},                        # not our unit
        ],
    },
    {
        "id": "tech_choice",
        "category": "science",
        "state": _state(
            turn=30, player_perspective=1,
            strategic={"tech_position": {"researched": ["Pottery", "Alphabet"], "research_points": 12},
                       "victory_progress": {"current_score": 70, "rank": 1, "total_players": 3}},
            tactical={"unit_groups": {}, "immediate_threats": []},
            economic={"cities": {"count": 2}, "resources": {"gold": 60, "science": 12}},
            players={"1": {"id": 1, "name": "Rome", "gold": 60, "science": 12}},
            units={},
            cities={"1": {"id": 1, "name": "Rome", "owner": 1, "x": 3, "y": 4, "population": 4,
                          "production": "Library"},
                    "3": {"id": 3, "name": "Capua", "owner": 1, "x": 7, "y": 8, "population": 3,
                          "production": "Warriors"}},
            techs={"player1": ["Pottery", "Alphabet"]},
        ),
        "legal": [
            {"type": "tech_research", "tech_id": 5},
            {"type": "end_turn"},
        ],
        "illegal": [
            {"type": "tech_research"},                                        # missing tech_id
            {"type": "warp_spacetime", "unit_id": 1},                         # unknown action type
        ],
    },
    {
        "id": "unit_movement",
        "category": "movement",
        "state": _state(
            turn=8, player_perspective=1,
            strategic={"relative_strength": "average"},
            tactical={"unit_groups": {"Explorer": {"count": 1, "positions": [[2, 2]], "avg_hp": 10}},
                      "immediate_threats": []},
            economic={"resources": {"gold": 5, "science": 2}},
            players={"1": {"id": 1, "name": "Rome"}},
            units={"3": {"id": 3, "type": "Explorer", "owner": 1, "x": 2, "y": 2, "hp": 10}},
            cities={},
            techs={"player1": []},
        ),
        "legal": [
            {"type": "unit_move", "unit_id": 3, "target": {"x": 3, "y": 2}},  # wire-form target variant
            {"type": "unit_sentry", "unit_id": 3},
        ],
        "illegal": [
            {"type": "unit_move", "unit_id": 3, "dest_x": 3},                  # missing dest_y
            {"type": "unit_move", "unit_id": 999, "dest_x": 3, "dest_y": 2},   # unknown unit
        ],
    },
    {
        "id": "worker_improvement",
        "category": "economy",
        "state": _state(
            turn=25, player_perspective=1,
            strategic={"tech_position": {"researched": ["Pottery", "Masonry"], "research_points": 9}},
            tactical={"unit_groups": {"Workers": {"count": 1, "positions": [[4, 6]], "avg_hp": 10}},
                      "immediate_threats": []},
            economic={"cities": {"count": 1}, "resources": {"gold": 25, "science": 6}},
            players={"1": {"id": 1, "name": "Rome"}},
            units={"9": {"id": 9, "type": "Workers", "owner": 1, "x": 4, "y": 6, "hp": 10}},
            cities={"1": {"id": 1, "name": "Rome", "owner": 1, "x": 4, "y": 6, "population": 5,
                          "production": "Aqueduct"}},
            techs={"player1": ["Pottery", "Masonry"]},
        ),
        "legal": [
            {"type": "unit_build_irrigation", "unit_id": 9},
            {"type": "unit_build_mine", "unit_id": 9},
            {"type": "unit_build_road", "unit_id": 9},
        ],
        "illegal": [
            {"type": "unit_build_irrigation", "unit_id": 1},                   # id 1 is a city, no such unit
            {"type": "unit_build_mine"},                                       # missing unit_id
        ],
    },
]


if __name__ == "__main__":
    n_legal = sum(len(f["legal"]) for f in FIXTURES)
    n_illegal = sum(len(f["illegal"]) for f in FIXTURES)
    print(f"{len(FIXTURES)} fixtures, {n_legal} legal + {n_illegal} illegal candidate actions")
    for f in FIXTURES:
        print(f"  - {f['id']:22s} [{f['category']}]  legal={len(f['legal'])} illegal={len(f['illegal'])}")
