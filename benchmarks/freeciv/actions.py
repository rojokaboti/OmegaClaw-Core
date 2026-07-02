"""Action normalization + pre-submission validation.

Mirrors the legality checks in ``freeciv-proxy/action_validator.py`` (LLMActionValidator)
so OmegaClaw rejects an illegal move *before* it reaches the game's ``action_submit`` —
the second core KPI of Issue #6 (0% illegal submissions).

Checks, in order:
  1. action `type` is known/supported;
  2. all required fields for that type are present;
  3. referenced unit/city exists and is owned by the acting player;
  4. move target is on the map;
  5. (optional) the action matches the server's advertised ``legal_actions``.

``validate_action`` returns a structured ``ValidationResult`` (never raises on a bad action).
"""

from . import adapter, schemas


class ValidationResult:
    """Mirror of action_validator.ValidationResult (is_valid + structured error)."""

    __slots__ = ("is_valid", "error_code", "error_message")

    def __init__(self, is_valid, error_code=None, error_message=None):
        self.is_valid = is_valid
        self.error_code = error_code
        self.error_message = error_message

    def as_tuple(self):
        return (self.is_valid, self.error_message)

    def to_dict(self):
        return {"is_valid": self.is_valid, "error_code": self.error_code,
                "error_message": self.error_message}

    def __repr__(self):
        return "ValidationResult(is_valid={!r}, code={!r})".format(self.is_valid, self.error_code)


def _ok():
    return ValidationResult(True)


def _err(code, msg):
    return ValidationResult(False, code, msg)


def normalize_action(action):
    """Coerce a candidate action into the canonical validator/packet form.

    Accepts the two wire variants seen in freeciv-llm: the API_DOCUMENTATION legal_actions
    example uses ``target:{x,y}``; the validator/packet form uses ``dest_x``/``dest_y``. We
    normalize to ``dest_x``/``dest_y`` (what ``action_submit`` expects).
    """
    if not isinstance(action, dict):
        return {}
    out = dict(action)
    atype = schemas.action_type_of(out)
    if atype:
        out["type"] = atype
        out.pop("action_type", None)
    if atype == schemas.ACTION_UNIT_MOVE and "dest_x" not in out:
        tgt = out.get("target")
        if isinstance(tgt, dict) and "x" in tgt and "y" in tgt:
            out["dest_x"], out["dest_y"] = tgt["x"], tgt["y"]
    if atype == schemas.ACTION_CITY_PRODUCTION and "production_type" not in out:
        tgt = out.get("target")
        if isinstance(tgt, dict) and "production_type" in tgt:
            out["production_type"] = tgt["production_type"]
    return out


def _find_by_id(collection, want_id):
    for item in collection:
        if str(item.get("id")) == str(want_id):
            return item
    return None


def _in_map_bounds(norm_raw_or_state, x, y):
    """Best-effort bounds check. If map size is unknown, only reject clearly-bad coords."""
    if x is None or y is None:
        return False
    try:
        x = int(x); y = int(y)
    except (TypeError, ValueError):
        return False
    return x >= 0 and y >= 0


def validate_action(action, state, legal_actions=None):
    """Validate a candidate action against a state (raw or normalized).

    ``state`` may be a raw llm_optimized dict or an already-normalized state.
    ``legal_actions`` (optional) is the server's advertised list; when provided, the action
    must match one of them (by type + unit/city id).
    """
    # normalize_state is idempotent (accepts raw dict-keyed collections or already-normalized
    # lists), so always run it rather than guessing whether `state` is already normalized.
    norm = adapter.normalize_state(state or {})
    act = normalize_action(action)
    atype = act.get("type")
    pid = norm.get("player_perspective")

    # 1) known action type
    if atype not in schemas.SUPPORTED_ACTIONS:
        return _err(schemas.E_UNKNOWN_ACTION, "unknown action type: {!r}".format(atype))

    # 2) required fields present
    for field in schemas.ACTION_REQUIRED_FIELDS[atype]:
        if act.get(field) is None:
            return _err(schemas.E_MISSING_FIELD, "missing required field {!r} for {}".format(field, atype))

    # 3) unit / city existence + ownership
    if atype in schemas.UNIT_ACTIONS:
        unit = _find_by_id(norm["units"], act["unit_id"])
        if unit is None:
            return _err(schemas.E_UNKNOWN_UNIT, "no such unit {}".format(act["unit_id"]))
        if unit.get("owner") != pid:
            return _err(schemas.E_UNIT_NOT_OWNED,
                        "unit {} owned by {}, not acting player {}".format(act["unit_id"], unit.get("owner"), pid))

    if atype in schemas.CITY_ACTIONS:
        city = _find_by_id(norm["cities"], act["city_id"])
        if city is None:
            return _err(schemas.E_UNKNOWN_CITY, "no such city {}".format(act["city_id"]))
        if city.get("owner") != pid:
            return _err(schemas.E_CITY_NOT_OWNED,
                        "city {} owned by {}, not acting player {}".format(act["city_id"], city.get("owner"), pid))

    # 4) move target on-map
    if atype == schemas.ACTION_UNIT_MOVE:
        if not _in_map_bounds(norm, act.get("dest_x"), act.get("dest_y")):
            return _err(schemas.E_TARGET_OFF_MAP,
                        "target ({},{}) is off-map".format(act.get("dest_x"), act.get("dest_y")))

    # 5) match server-advertised legal actions (when available)
    if legal_actions is not None:
        if not _matches_legal(act, legal_actions):
            return _err(schemas.E_NOT_LEGAL, "action not in server legal_actions")

    return _ok()


def _matches_legal(act, legal_actions):
    """Whether `act` matches a server-advertised legal action by its FULL payload.

    Matching type + actor id is not enough: a legal move for unit 7 must not authorize a
    *different* move for unit 7. We compare every identifying field for the action type
    (``ACTION_REQUIRED_FIELDS`` is exactly that payload: unit_move -> unit_id/dest_x/dest_y,
    city_production -> city_id/production_type, tech_research -> tech_id, unit-simple ->
    unit_id, end_turn -> type only). Both sides are normalized first so wire variants
    (``target:{x,y}`` vs ``dest_x``/``dest_y``) compare equal.
    """
    atype = act.get("type")
    keys = schemas.ACTION_REQUIRED_FIELDS.get(atype, ())
    for la in legal_actions:
        la = normalize_action(la)
        if la.get("type") != atype:
            continue
        if all(str(la.get(k)) == str(act.get(k)) for k in keys):
            return True
    return False
