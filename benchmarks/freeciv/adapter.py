"""Deterministic normalization + fact extraction from a freeciv-llm `llm_optimized` state.

Everything here is a pure function of the input state: no clock, no network, no randomness,
stable ordering throughout. Given identical input, ``normalize_state``/``facts_from_state``/
``state_hash`` are byte-for-byte reproducible — the core KPI of Issue #6.

A *fact* is a small, JSON-serializable dict:

    {"subj": "City_1", "pred": "Inheritance", "obj": "LowFood",
     "f": 1.0, "c": 0.99, "category": "economic.cities"}

`pred` is a PLN link constructor (``Inheritance`` for properties, ``Evaluation`` for
relations); `atoms.py` renders these into MeTTa/PLN word-form strings.
"""

import hashlib
import json

from . import schemas


# --------------------------------------------------------------------------- helpers

def _collection_to_sorted_list(coll):
    """freeciv-llm collections are dicts keyed by string id -> deterministic list by int id.

    Accepts a dict (keyed by id), a list, or None. Sorts by the numeric `id` when available,
    else by string key, so ordering never depends on dict insertion order.
    """
    if coll is None:
        return []
    if isinstance(coll, list):
        items = list(coll)
    elif isinstance(coll, dict):
        items = list(coll.values())
    else:
        return []

    def _key(item):
        if isinstance(item, dict) and "id" in item:
            try:
                return (0, int(item["id"]))
            except (TypeError, ValueError):
                return (1, str(item["id"]))
        return (2, json.dumps(item, sort_keys=True))

    return sorted(items, key=_key)


def _get(d, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _owned(norm, key):
    pid = norm.get("player_perspective")
    return [x for x in norm.get(key, []) if x.get("owner") == pid]


def _present(norm, category):
    """Whether a convertible semantic category carries usable (convertible) data.

    Operates on a *normalized* state. A category counts as present only when it has
    information to convert — e.g. an empty threat list is "no threats", not a coverage miss.
    """
    if category in ("turn", "phase", "player_perspective"):
        return norm.get(category) is not None
    if category == "units":
        return bool(_owned(norm, "units")) or bool(_get(norm, "tactical", "unit_groups"))
    if category == "cities":
        return bool(_owned(norm, "cities")) or bool(_get(norm, "economic", "cities", "count"))
    if category == "techs":
        return bool(_researched_techs(norm))
    if category == "resources":
        econ = norm.get("economic") or {}
        res = econ.get("resources") if isinstance(econ.get("resources"), dict) else econ
        return isinstance(res, dict) and any(
            isinstance(res.get(k), (int, float)) for k in ("gold", "science", "research"))
    if category == "strategic":
        strat = norm.get("strategic") or {}
        return bool(strat.get("victory_progress")) or bool(strat.get("relative_strength")) \
            or isinstance(strat.get("score"), (int, float))
    if category == "threats":
        threats = _get(norm, "tactical", "immediate_threats") or _get(norm, "tactical", "visible_threats") or []
        if isinstance(threats, list) and threats:
            return True
        # undefended detection is only meaningful if we own cities
        return bool(_owned(norm, "cities"))
    return False


# --------------------------------------------------------------------------- normalize

def normalize_state(raw):
    """Project a raw `llm_optimized` state into a deterministic, canonical structure.

    Tolerant of missing fields (the proxy sends only what the perspective player can see):
    absent sections normalize to empty, and are simply not counted toward coverage.
    """
    if not isinstance(raw, dict):
        raise TypeError("state must be a dict")

    player_id = raw.get("player_perspective")
    if player_id is None:
        player_id = _get(raw, "game", "current_player")

    players = _collection_to_sorted_list(raw.get("players"))
    units = _collection_to_sorted_list(raw.get("units"))
    cities = _collection_to_sorted_list(raw.get("cities"))

    norm = {
        "turn": raw.get("turn"),
        "phase": raw.get("phase"),
        "player_perspective": player_id,
        "players": players,
        "units": units,
        "cities": cities,
        "techs": raw.get("techs") or {},
        "strategic": raw.get("strategic") or {},
        "tactical": raw.get("tactical") or {},
        "economic": raw.get("economic") or {},
    }
    return norm


def state_hash(raw):
    """SHA-256 of the canonical JSON of the *normalized* state (order-independent)."""
    norm = normalize_state(raw)
    canonical = json.dumps(norm, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- facts

# Confidence for hard, directly-observed game facts vs. derived/heuristic ones.
CONF_OBSERVED = (1.0, 0.99)
CONF_DERIVED = (1.0, 0.9)


def _tok(prefix, value):
    """Build a safe atom token, e.g. City_1, Tech_Pottery, Type_Warrior."""
    s = str(value)
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "" for ch in s.replace(" ", ""))
    return "{}_{}".format(prefix, safe) if prefix else safe


def _fact(subj, pred, obj, conf, category):
    return {"subj": subj, "pred": pred, "obj": obj,
            "f": conf[0], "c": conf[1], "category": category}


def _city_low_food(city):
    """Heuristic mirroring the economic view: a city with a food deficit or shrink risk."""
    for k in ("food_surplus", "food_stock_change", "surplus_food"):
        if isinstance(city.get(k), (int, float)):
            return city[k] < 0
    gp = city.get("growth_potential")
    if isinstance(gp, str):
        return gp.lower() in ("shortage", "starving", "declining", "low")
    return None


def _researched_techs(norm):
    """Researched techs for the perspective player, from the summary block or raw techs dict."""
    pid = norm.get("player_perspective")
    tp = _get(norm, "strategic", "tech_position", default={})
    if isinstance(tp, dict) and isinstance(tp.get("researched"), list):
        return list(tp["researched"])
    techs = norm.get("techs") or {}
    if isinstance(techs, dict):
        val = techs.get("player{}".format(pid))
        if isinstance(val, list):
            return list(val)
    if isinstance(techs, list):
        return list(techs)
    return []


def facts_from_state(norm):
    """Derive a deterministically-ordered list of facts from a normalized state.

    Facts are drawn from BOTH the raw rosters (units/cities) and the summary blocks
    (strategic/tactical/economic), so any populated section produces at least one fact.
    Each rule is guarded by data presence — partial states yield partial (never bogus)
    facts. Facts are sorted at the end for byte-stable output.
    """
    pid = norm.get("player_perspective")
    facts = []

    # --- units: per owned unit (roster) + grouped counts (tactical summary) ------------
    for unit in norm["units"]:
        if unit.get("owner") != pid:
            continue
        uid = _tok("Unit", unit.get("id"))
        utype = unit.get("type")
        if utype:
            facts.append(_fact(uid, "Inheritance", _tok("Type", utype), CONF_OBSERVED, "units"))
        ux, uy = unit.get("x"), unit.get("y")
        if ux is not None and uy is not None:
            facts.append(_fact(uid, "Evaluation", "At:{}:{}".format(int(ux), int(uy)),
                               CONF_OBSERVED, "units"))
    unit_groups = _get(norm, "tactical", "unit_groups", default={})
    if isinstance(unit_groups, dict):
        for utype, grp in sorted(unit_groups.items()):
            count = grp.get("count") if isinstance(grp, dict) else None
            if isinstance(count, (int, float)):
                facts.append(_fact(_tok("Player", pid), "Evaluation",
                                   "Has:" + _tok("Type", utype) + ":" + str(int(count)),
                                   CONF_OBSERVED, "units"))

    # --- cities: per owned city production/population/food (roster) --------------------
    for city in norm["cities"]:
        if city.get("owner") != pid:
            continue
        cid = _tok("City", city.get("id"))
        cx, cy = city.get("x"), city.get("y")
        if cx is not None and cy is not None:
            facts.append(_fact(cid, "Evaluation", "At:{}:{}".format(int(cx), int(cy)),
                               CONF_OBSERVED, "cities"))
        prod = city.get("production")
        if prod:
            facts.append(_fact(cid, "Evaluation", "Produces:" + _tok("", prod),
                               CONF_OBSERVED, "cities"))
        pop = city.get("population")
        if isinstance(pop, (int, float)):
            facts.append(_fact(cid, "Evaluation", "Population:" + str(int(pop)),
                               CONF_OBSERVED, "cities"))
        if _city_low_food(city) is True:
            facts.append(_fact(cid, "Inheritance", "LowFood", CONF_DERIVED, "cities"))

    # --- resources: gold / science -----------------------------------------------------
    # Two observed shapes: documented {economic:{resources:{gold,science}}} and the real
    # runtime {economic:{gold, research}} (civcom.build_llm_optimized_state). Handle both.
    econ = norm.get("economic") or {}
    resources = econ.get("resources") if isinstance(econ.get("resources"), dict) else econ
    _res_vals = {
        "gold": resources.get("gold"),
        "science": resources.get("science", resources.get("research")),
    }
    for res, val in _res_vals.items():
        if isinstance(val, (int, float)):
            facts.append(_fact(_tok("Player", pid), "Evaluation",
                               res.capitalize() + ":" + str(int(val)),
                               CONF_OBSERVED, "resources"))

    # --- techs: researched -------------------------------------------------------------
    for t in _researched_techs(norm):
        facts.append(_fact(_tok("Tech", t), "Inheritance", "Researched", CONF_OBSERVED, "techs"))

    # --- strategic: score + relative strength ------------------------------------------
    # Documented {strategic:{victory_progress:{current_score}}} vs real {strategic:{score}}.
    strat = norm.get("strategic") or {}
    vp = strat.get("victory_progress") if isinstance(strat.get("victory_progress"), dict) else {}
    score = vp.get("current_score", strat.get("score"))
    if isinstance(score, (int, float)):
        facts.append(_fact(_tok("Player", pid), "Evaluation", "Score:" + str(int(score)),
                           CONF_OBSERVED, "strategic"))
    rs = strat.get("relative_strength")
    if isinstance(rs, str) and rs:
        facts.append(_fact(_tok("Player", pid), "Inheritance", _tok("", rs.capitalize()),
                           CONF_DERIVED, "strategic"))

    # --- threats: immediate threats + undefended owned cities --------------------------
    threats = (_get(norm, "tactical", "immediate_threats")
               or _get(norm, "tactical", "visible_threats") or [])
    if isinstance(threats, list):
        for th in threats:
            if not isinstance(th, dict):
                continue
            src = th.get("enemy_unit_id", th.get("source", th.get("unit_id")))
            tgt = th.get("target_id", th.get("target"))
            if src is not None and tgt is not None:
                facts.append(_fact(_tok("Enemy", src), "Evaluation", "Threatens:" + _tok("Target", tgt),
                                   CONF_DERIVED, "threats"))
    occupied = {(u["x"], u["y"]) for u in norm["units"]
                if u.get("owner") == pid and u.get("x") is not None and u.get("y") is not None}
    for city in norm["cities"]:
        if city.get("owner") != pid:
            continue
        cx, cy = city.get("x"), city.get("y")
        if cx is not None and cy is not None and (cx, cy) not in occupied:
            facts.append(_fact(_tok("City", city.get("id")), "Inheritance", "Undefended",
                               CONF_DERIVED, "threats"))

    facts.sort(key=lambda f: (f["category"], f["subj"], f["pred"], f["obj"]))
    return facts


# --------------------------------------------------------------------------- coverage

def coverage(raw):
    """Fraction of *present* convertible categories for which we produced >=1 fact.

    Honest denominator: only categories the state actually carries. Returns a dict with the
    ratio plus the present/covered breakdown so the benchmark can report it.
    """
    norm = normalize_state(raw)
    facts = facts_from_state(norm)
    produced = {f["category"] for f in facts}

    present, covered = [], []
    for cat in schemas.CONVERTIBLE_CATEGORIES:
        if _present(norm, cat):
            present.append(cat)
            # scalars are structural (present == covered); the rest need a derived fact.
            if cat in produced or cat in ("turn", "phase", "player_perspective"):
                covered.append(cat)
    ratio = (len(covered) / len(present)) if present else 1.0
    return {"ratio": ratio, "present": present, "covered": covered,
            "missing": [c for c in present if c not in covered]}
