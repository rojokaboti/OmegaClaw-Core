"""FreeCiv state / action contract — mirrors ``taso-ventures/freeciv-llm``.

This is the single source of truth for the shapes the adapter consumes and produces. It
mirrors (does not import) the freeciv-llm proxy so the deterministic adapter stays
host-runnable with no game server. Field/action names are taken verbatim from
``freeciv-proxy/state_extractor.py`` and ``freeciv-proxy/action_validator.py``.
"""

# --------------------------------------------------------------------------- state

# Top-level keys of an `llm_optimized` state (state_extractor._format_llm_optimized_state).
STATE_TOP_KEYS = (
    "format", "turn", "phase",
    "strategic", "tactical", "economic",
    "game", "map", "players", "units", "cities", "techs",
    "timestamp", "player_perspective",
)

# The "convertible" field categories we account for in coverage — semantic sections, each of
# which a derived fact is tagged with. Coverage is measured only over categories the state
# actually carries (see adapter._present), so a partial state is scored against what it
# contains, not padded with absent fields. Every present category yields >=1 fact.
# NOTE: `threats` (immediate threats / undefended cities) is a *derived inference*, not a raw
# state field, so it is deliberately excluded from the coverage denominator — its facts are
# still produced and tagged "threats", but a correctly-defended city must not count as a miss.
CONVERTIBLE_CATEGORIES = (
    "turn", "phase", "player_perspective",   # structural scalars (present == covered)
    "units",       # owned-unit roster (or tactical.unit_groups)
    "cities",      # owned-city roster (or economic.cities)
    "techs",       # researched tech (strategic.tech_position or raw techs)
    "resources",   # economic.resources gold/science
    "strategic",   # score / relative strength
)

# Collections are dicts keyed by string id in llm_optimized (state_extractor docstring).
COLLECTION_KEYS = ("players", "units", "cities")


# --------------------------------------------------------------------------- actions

# Supported action `type` values in the canonical (packet-converter / validator) form.
# Names verbatim from action_validator.ActionType / _normalize_action_format.
ACTION_UNIT_MOVE = "unit_move"
ACTION_UNIT_BUILD_CITY = "unit_build_city"
ACTION_UNIT_FORTIFY = "unit_fortify"
ACTION_UNIT_SENTRY = "unit_sentry"
ACTION_UNIT_SKIP = "unit_skip"
ACTION_UNIT_BUILD_ROAD = "unit_build_road"
ACTION_UNIT_BUILD_IRRIGATION = "unit_build_irrigation"
ACTION_UNIT_BUILD_MINE = "unit_build_mine"
ACTION_CITY_PRODUCTION = "city_production"
ACTION_TECH_RESEARCH = "tech_research"
ACTION_END_TURN = "end_turn"

# Required fields per action type (action_validator._validate_*). Ownership / existence are
# checked separately against game state; these are the *shape* requirements.
ACTION_REQUIRED_FIELDS = {
    ACTION_UNIT_MOVE: ("unit_id", "dest_x", "dest_y"),
    ACTION_UNIT_BUILD_CITY: ("unit_id",),
    ACTION_UNIT_FORTIFY: ("unit_id",),
    ACTION_UNIT_SENTRY: ("unit_id",),
    ACTION_UNIT_SKIP: ("unit_id",),
    ACTION_UNIT_BUILD_ROAD: ("unit_id",),
    ACTION_UNIT_BUILD_IRRIGATION: ("unit_id",),
    ACTION_UNIT_BUILD_MINE: ("unit_id",),
    ACTION_CITY_PRODUCTION: ("city_id", "production_type"),
    ACTION_TECH_RESEARCH: ("tech_id",),
    ACTION_END_TURN: (),
}

# Actions that operate on a unit the acting player must own.
UNIT_ACTIONS = frozenset({
    ACTION_UNIT_MOVE, ACTION_UNIT_BUILD_CITY, ACTION_UNIT_FORTIFY, ACTION_UNIT_SENTRY,
    ACTION_UNIT_SKIP, ACTION_UNIT_BUILD_ROAD, ACTION_UNIT_BUILD_IRRIGATION, ACTION_UNIT_BUILD_MINE,
})
# Actions that operate on a city the acting player must own.
CITY_ACTIONS = frozenset({ACTION_CITY_PRODUCTION})

SUPPORTED_ACTIONS = frozenset(ACTION_REQUIRED_FIELDS)

# Numeric FreeCiv action ids (action_constants.py) for the subset we surface. Kept for the
# report / cross-referencing; the packet `type` strings above are what we submit.
ACTION_IDS = {
    ACTION_UNIT_BUILD_CITY: 27,   # ACTION_FOUND_CITY
    "attack": 45,                 # ACTION_ATTACK
    "disband": 30,                # ACTION_DISBAND_UNIT
}

# Structured validation error codes (align with action_validator error taxonomy).
E_UNKNOWN_ACTION = "E100"
E_MISSING_FIELD = "E220"
E_UNKNOWN_UNIT = "E201"
E_UNIT_NOT_OWNED = "E202"
E_UNKNOWN_CITY = "E203"
E_CITY_NOT_OWNED = "E204"
E_TARGET_OFF_MAP = "E210"
E_NOT_LEGAL = "E230"
E_WRONG_PLAYER = "E240"


def action_type_of(action):
    """Return the action's `type` (canonical) or the `action_type` alias used on the wire."""
    if not isinstance(action, dict):
        return None
    return action.get("type") or action.get("action_type")
