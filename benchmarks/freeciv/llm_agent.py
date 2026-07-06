"""Shared LLM decision agent for the A/B arms (Issue #25 experiment).

Both arms use the SAME model/provider/prompt/temperature — the ONLY difference is the `context`
text passed to :func:`decide`:
  - **plain** arm: `render_plain(norm)` (facts as plain text, no PLN atoms, no reasoning).
  - **pln**   arm: the same plain facts **plus** a "DERIVED (PLN reasoning)" block from
    `reason.derive` (authentic MeTTa/PLN conclusions).

Provider resolves through `provider_config.provider_entry(FREECIV_PROVIDER)` (default SNET) and the
key from the env var it names — so "the LLM provider works through the env" for both arms identically.
"""

import json
import os
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))          # benchmarks/freeciv
_BENCH = os.path.dirname(_HERE)                             # benchmarks
_SRC = os.path.join(os.path.dirname(_BENCH), "src")         # src (for provider_config)
for _p in (_BENCH, _SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import provider_config as pc  # noqa: E402

PROVIDER = os.environ.get("FREECIV_PROVIDER", "SNET")
_P = pc.provider_entry(PROVIDER) or {}
_KEY = os.environ.get(_P.get("api_key_env", ""), "") if _P else ""

# Identical action schema for both arms (mirrors benchmarks/freeciv/llm_play.py:_SYS).
SYSTEM_PROMPT = (
    "You are a FreeCiv agent. You get the game state plus your units. Choose 1-3 concrete actions "
    "for this turn. Return ONLY JSON: {\"actions\":[{...}]}. Allowed shapes: "
    '{"type":"unit_move","unit_id":<id>,"dest_x":<int>,"dest_y":<int>}; '
    '{"type":"unit_fortify","unit_id":<id>}; {"type":"unit_sentry","unit_id":<id>}; '
    '{"type":"unit_build_city","unit_id":<id>} (settlers); '
    '{"type":"unit_build_road|unit_build_irrigation|unit_build_mine","unit_id":<id>} (workers). '
    "Use only listed unit_ids; keep dest within +/-1 of the unit's tile."
)


def provider_info():
    return {"provider": PROVIDER, "model": _P.get("model"), "have_key": bool(_KEY),
            "key_env": _P.get("api_key_env")}


def have_key():
    return bool(_KEY)


def _unit_lines(units):
    return "\n".join(
        "  unit %s: type=%s at (%s,%s)" % (u.get("id"), u.get("type"), u.get("x"), u.get("y"))
        for u in units)


def decide(context, units, temperature=0.3, max_tokens=4000, timeout=120):
    """Call the configured LLM with `context` + the unit list; return parsed action dicts.

    `context` is the arm-specific state rendering (plain facts, or plain facts + PLN conclusions).
    Returns ([], meta) on any error; meta carries latency + prompt size for the metrics log.
    """
    import time
    user = context + "\n\nMy units:\n" + _unit_lines(units)
    payload = json.dumps({
        "model": _P.get("model"),
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": temperature,
    }).encode()
    req = urllib.request.Request(
        _P.get("base_url", "").rstrip("/") + "/chat/completions", data=payload,
        headers={"Authorization": "Bearer " + _KEY, "Content-Type": "application/json"})
    meta = {"prompt_chars": len(user), "llm_ms": None, "error": None}
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
        meta["llm_ms"] = int((time.time() - t0) * 1000)
        content = d["choices"][0]["message"].get("content") or ""
        i, j = content.find("{"), content.rfind("}")
        actions = json.loads(content[i:j + 1]).get("actions", []) if i >= 0 and j > i else []
        return (actions if isinstance(actions, list) else []), meta
    except Exception as e:  # noqa: BLE001 - a bad turn must not kill the run
        meta["llm_ms"] = int((time.time() - t0) * 1000)
        meta["error"] = "{}: {}".format(type(e).__name__, str(e)[:200])
        return [], meta


def render_plain(norm):
    """Plain-text rendering of the same facts the PLN arm reasons over (NO PLN atoms).

    Units (type+pos), owned cities (production/pop/food), economy (gold/science/score), and
    researched techs — the plain-LLM arm's entire state view.
    """
    pid = norm.get("player_perspective")
    lines = ["GAME STATE (turn %s, phase %s):" % (norm.get("turn"), norm.get("phase"))]

    mine = [u for u in norm.get("units", []) if u.get("owner") == pid]
    lines.append("Your units (%d):" % len(mine))
    for u in mine:
        lines.append("  - unit %s: %s at (%s,%s)" % (u.get("id"), u.get("type"), u.get("x"), u.get("y")))

    cities = [c for c in norm.get("cities", []) if c.get("owner") == pid]
    lines.append("Your cities (%d):" % len(cities))
    for c in cities:
        extra = []
        if c.get("production"):
            extra.append("producing %s" % c.get("production"))
        if isinstance(c.get("population"), (int, float)):
            extra.append("pop %s" % int(c["population"]))
        for k in ("food_surplus", "surplus_food"):
            if isinstance(c.get(k), (int, float)):
                extra.append("food %+d" % int(c[k])); break
        lines.append("  - city %s at (%s,%s)%s" % (
            c.get("id"), c.get("x"), c.get("y"), (" [" + ", ".join(extra) + "]") if extra else ""))

    econ = norm.get("economic") or {}
    res = econ.get("resources") if isinstance(econ.get("resources"), dict) else econ
    gold = res.get("gold") if isinstance(res, dict) else None
    sci = res.get("science", res.get("research")) if isinstance(res, dict) else None
    strat = norm.get("strategic") or {}
    vp = strat.get("victory_progress") if isinstance(strat.get("victory_progress"), dict) else {}
    score = vp.get("current_score", strat.get("score"))
    lines.append("Economy: gold=%s science=%s score=%s" % (gold, sci, score))
    return "\n".join(lines)
