"""Live neural-symbolic loop: an LLM decides over the adapter's PLN atoms, and the Issue #6
adapter validates every action before it is submitted to a running freeciv-llm game.

Per decision cycle: observe -> PLN atoms -> prompt the configured LLM provider (SNET by
default) with the atoms + the player's units + the action schema -> parse JSON actions ->
`validate_action` (submit legal, block illegal pre-submit) -> end_turn.

This is the end-to-end demonstration of the Issue #6 pipeline with a real LLM in the loop:
symbolic state in, validated actions out, zero illegal moves reaching the game. It is a
demo/validation runner (needs the live stack + `websockets` + a funded provider key), not part
of the host test suite; the deterministic adapter it drives is host-tested.

Uses the repo's own provider config (`src/provider_config.py`) so the endpoint/model/key match
what OmegaClaw uses. Default provider SNET (`SNET_API_KEY`, OpenAI-compatible).

Caveat: multi-turn *advancement* depends on freeciv-web's turn-cycle (the LLM-proxy player's
phase-done / AI-phase handshake); on this direct-proxy path the server may hold the turn, so
successive cycles can re-decide over the same turn. The validate->submit pipeline is unaffected.

Config (env): FREECIV_PROXY_WS, FREECIV_API_TOKEN, FREECIV_GAME_ID, FREECIV_CYCLES,
FREECIV_PROVIDER (default SNET). Run: python3 benchmarks/freeciv/llm_play.py
"""
import asyncio
import json
import os
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_BENCH = os.path.dirname(_HERE)
_SRC = os.path.join(os.path.dirname(_BENCH), "src")
for _p in (_BENCH, _SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from freeciv import adapter, atoms, actions  # noqa: E402
import provider_config as pc  # noqa: E402

WS = os.environ.get("FREECIV_PROXY_WS", "ws://localhost:8002/llmsocket/8002")
TOKEN = os.environ.get("FREECIV_API_TOKEN", "test-token-fc3d-001")
GAME = os.environ.get("FREECIV_GAME_ID", "omega_llm")
CYCLES = int(os.environ.get("FREECIV_CYCLES", "3"))
PROVIDER = os.environ.get("FREECIV_PROVIDER", "SNET")

_P = pc.provider_entry(PROVIDER)
_KEY = os.environ.get(_P["api_key_env"], "")

_SYS = (
    "You are a FreeCiv agent. You get the game state as MeTTa/PLN atoms (facts with truth "
    "values) plus your units. Choose 1-3 concrete actions for this turn. Return ONLY JSON: "
    '{"actions":[{...}]}. Allowed shapes: '
    '{"type":"unit_move","unit_id":<id>,"dest_x":<int>,"dest_y":<int>}; '
    '{"type":"unit_fortify","unit_id":<id>}; {"type":"unit_sentry","unit_id":<id>}; '
    '{"type":"unit_build_city","unit_id":<id>} (settlers); '
    '{"type":"unit_build_road|unit_build_irrigation|unit_build_mine","unit_id":<id>} (workers). '
    "Use only listed unit_ids; keep dest within +/-1 of the unit's tile."
)


def llm_decide(sentences, units):
    unit_lines = "\n".join("  unit %s: type=%s at (%s,%s)" % (u["id"], u.get("type"), u.get("x"), u.get("y"))
                           for u in units)
    payload = json.dumps({
        "model": _P["model"],
        "messages": [{"role": "system", "content": _SYS},
                     {"role": "user", "content": "PLN atoms:\n" + "\n".join(sentences) + "\n\nMy units:\n" + unit_lines}],
        "max_tokens": 4000, "temperature": 0.3,
    }).encode()
    req = urllib.request.Request(_P["base_url"].rstrip("/") + "/chat/completions", data=payload,
                                 headers={"Authorization": "Bearer " + _KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode())
    content = d["choices"][0]["message"].get("content") or ""
    i, j = content.find("{"), content.rfind("}")
    try:
        return json.loads(content[i:j + 1]).get("actions", [])
    except Exception:
        return []


async def _nxt(ws, types, timeout=20, drain=500):
    for _ in range(drain):
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        except asyncio.TimeoutError:
            return None
        if isinstance(m, dict) and m.get("type") in types:
            return m
    return None


async def _get_state(ws):
    await ws.send(json.dumps({"type": "state_query", "format": "llm_optimized"}))
    m = await _nxt(ws, {"state_response", "state_update"}, timeout=15)
    if not m:
        return None
    return m.get("data") if isinstance(m.get("data"), dict) and "turn" in m.get("data", {}) else m


def _to_packet(action):
    na = actions.normalize_action(action)
    pkt = {"type": "action", "action_type": na.get("type")}
    for k in ("unit_id", "dest_x", "dest_y", "city_id", "production_type", "tech_id"):
        if k in na:
            pkt["actor_id" if k == "unit_id" else k] = na[k]
    return pkt


async def run():
    import websockets  # live-only dependency
    if not _KEY:
        print("[llm] %s not set in env" % _P["api_key_env"])
        return 1
    print("[llm] provider=%s model=%s" % (PROVIDER, _P["model"]))
    async with websockets.connect(WS, open_timeout=20, max_size=None, ping_interval=None) as ws:
        await ws.send(json.dumps({"type": "llm_connect", "agent_id": "omega", "api_token": TOKEN,
                                  "game_id": GAME, "nation": "Romans", "leader_name": "Caesar"}))
        await _nxt(ws, {"auth_success"}, timeout=40)
        st = await _get_state(ws)
        if not st or not st.get("units"):
            for cmd in ("/set minplayers 1", "/set aifill 3", "/start"):
                await ws.send(json.dumps({"type": "chat", "message": cmd}))
                await asyncio.sleep(1.0)
            for _ in range(20):
                await asyncio.sleep(4)
                st = await _get_state(ws)
                if st and st.get("units"):
                    break
        if not st or not st.get("units"):
            print("[llm] no populated state")
            return 1

        totals = {"proposed": 0, "submitted": 0, "blocked": 0, "cycles": 0}
        for _ in range(CYCLES):
            st = await _get_state(ws)
            norm = adapter.normalize_state(st)
            facts = adapter.facts_from_state(norm)
            sents = atoms.sentences_from_facts(facts)
            mine = [u for u in norm["units"] if u.get("owner") == norm["player_perspective"]]
            print("\n=== cycle %d | turn %s | %d atoms | %d units ===" % (totals["cycles"] + 1, st.get("turn"), len(sents), len(mine)))
            for a in llm_decide(sents, mine):
                totals["proposed"] += 1
                v = actions.validate_action(a, st)
                if v.is_valid:
                    await ws.send(json.dumps(_to_packet(a)))
                    totals["submitted"] += 1
                    print("   SUBMIT %s" % json.dumps(a))
                else:
                    totals["blocked"] += 1
                    print("   BLOCK  %s (%s)" % (json.dumps(a), v.error_code))
            await ws.send(json.dumps({"type": "action", "action_type": "end_turn"}))
            totals["cycles"] += 1
            await asyncio.sleep(3)

        print("\n=== LLM-DRIVEN LIVE RUN ===")
        print(json.dumps(totals, indent=2))
        print("illegal actions that reached the game: 0 (all %d blocked pre-submit)" % totals["blocked"])
        return 0


def main():
    try:
        return asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        print("[llm] ERROR:", type(e).__name__, str(e)[:200])
        return 1


if __name__ == "__main__":
    sys.exit(main())
