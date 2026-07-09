"""Per-turn game metrics extracted from a normalized FreeCiv state (Issue #25 A/B experiment).

Pure function of the normalized state (same fields the adapter already reads), so it's
deterministic and host-testable. Used to build the per-turn trajectory both arms are compared on.
"""


def _num(x):
    return x if isinstance(x, (int, float)) else None


def metrics_from_state(norm):
    """Return the comparison metrics for a normalized state.

    {turn, score, gold, science, n_cities, n_units, n_techs}. Missing values -> None/0.
    """
    pid = norm.get("player_perspective")

    econ = norm.get("economic") or {}
    res = econ.get("resources") if isinstance(econ.get("resources"), dict) else econ
    gold = _num(res.get("gold")) if isinstance(res, dict) else None
    science = _num(res.get("science", res.get("research"))) if isinstance(res, dict) else None

    strat = norm.get("strategic") or {}
    vp = strat.get("victory_progress") if isinstance(strat.get("victory_progress"), dict) else {}
    score = _num(vp.get("current_score", strat.get("score")))

    n_cities = sum(1 for c in norm.get("cities", []) if c.get("owner") == pid)
    n_units = sum(1 for u in norm.get("units", []) if u.get("owner") == pid)

    # researched techs for the perspective player (summary block or raw techs dict)
    techs = norm.get("techs") or {}
    tp = (strat.get("tech_position") or {}) if isinstance(strat, dict) else {}
    if isinstance(tp, dict) and isinstance(tp.get("researched"), list):
        n_techs = len(tp["researched"])
    elif isinstance(techs, dict) and isinstance(techs.get("player{}".format(pid)), list):
        n_techs = len(techs["player{}".format(pid)])
    elif isinstance(techs, list):
        n_techs = len(techs)
    else:
        n_techs = 0

    turn = norm.get("turn")
    try:
        turn = int(turn) if turn is not None else None
    except (TypeError, ValueError):
        pass

    return {"turn": turn, "score": score, "gold": gold, "science": science,
            "n_cities": n_cities, "n_units": n_units, "n_techs": n_techs}
