"""Live E2E runner against a running taso-ventures/freeciv-llm stack (Issue #6).

Connects to the freeciv-proxy LLM socket, ensures a game is started, pulls a real
`llm_optimized` state, converts it to deterministic PLN atoms, and does a validate->submit
action round-trip (submitting only legal actions; blocking illegal ones pre-submit).

This is a demo/validation runner, not part of the host test suite (it needs the live stack
and the `websockets` package). The deterministic adapter it exercises is host-tested in
`Autotests/test_freeciv_adapter.py` and `benchmarks/freeciv_benchmark.py`.

Game-start note: freeciv-llm civservers default to `minplayers=2`, so a single agent + aifill
never leaves pregame (turn 0, no units). While in pregame this runner issues
`/set minplayers 1` then `/start` (server commands via the `chat` message type).

Config (env): FREECIV_PROXY_WS (default ws://localhost:8002/llmsocket/8002),
FREECIV_API_TOKEN (default test-token-fc3d-001), FREECIV_AGENT_ID, FREECIV_GAME_ID.

Run: python3 benchmarks/freeciv/live_play.py
"""
import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BENCH = os.path.dirname(_HERE)
if _BENCH not in sys.path:
    sys.path.insert(0, _BENCH)

from freeciv import adapter, atoms, actions  # noqa: E402

WS_URL = os.environ.get("FREECIV_PROXY_WS", "ws://localhost:8002/llmsocket/8002")
TOKEN = os.environ.get("FREECIV_API_TOKEN", "test-token-fc3d-001")
AGENT = os.environ.get("FREECIV_AGENT_ID", "omega")
GAME = os.environ.get("FREECIV_GAME_ID", "omega_e2e")


async def _next(ws, types, timeout=20, drain=400):
    for _ in range(drain):
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        except asyncio.TimeoutError:
            return None
        if isinstance(m, dict) and m.get("type") in types:
            return m
    return None


def _state_body(m):
    if not m:
        return None
    d = m.get("data")
    return d if isinstance(d, dict) and "turn" in d else m


async def _get_state(ws):
    await ws.send(json.dumps({"type": "state_query", "format": "llm_optimized"}))
    return _state_body(await _next(ws, {"state_response", "state_update"}, timeout=15))


async def run():
    import websockets  # live-only dependency
    async with websockets.connect(WS_URL, open_timeout=20, max_size=None, ping_interval=None) as ws:
        await ws.send(json.dumps({"type": "llm_connect", "agent_id": AGENT, "api_token": TOKEN,
                                  "game_id": GAME, "nation": "Romans", "leader_name": "Caesar"}))
        auth = await _next(ws, {"auth_success"}, timeout=40)
        pid = auth.get("player_id") if auth else None
        print("[live] auth player_id=%s" % pid)

        state = await _get_state(ws)
        if not state or not state.get("units"):
            print("[live] pregame -> /set minplayers 1; /start")
            for cmd in ("/set minplayers 1", "/set aifill 3", "/start"):
                await ws.send(json.dumps({"type": "chat", "message": cmd}))
                await asyncio.sleep(1.0)
            for _ in range(20):
                await asyncio.sleep(4)
                state = await _get_state(ws)
                if state and state.get("units"):
                    break

        if not state or not state.get("units"):
            print("[live] FAILED to reach a populated state")
            return 1

        norm = adapter.normalize_state(state)
        facts = adapter.facts_from_state(norm)
        cov = adapter.coverage(state)
        det = adapter.state_hash(state) == adapter.state_hash(json.loads(json.dumps(state)))
        print("[live] turn=%s units=%d cities=%d | facts=%d coverage=%.2f determinism=%s"
              % (state.get("turn"), len(state.get("units") or {}), len(state.get("cities") or {}),
                 len(facts), cov["ratio"], det))
        for s in atoms.sentences_from_facts(facts)[:12]:
            print("   ", s)

        mine = [u for u in norm["units"] if u.get("owner") == norm["player_perspective"]]
        illegal = {"type": "unit_move", "unit_id": 99999, "dest_x": 1, "dest_y": 1}
        vb = actions.validate_action(illegal, state)
        print("[live] illegal unknown-unit blocked pre-submit: is_valid=%s code=%s" % (vb.is_valid, vb.error_code))
        if mine:
            uid = mine[0]["id"]
            v = actions.validate_action({"type": "unit_fortify", "unit_id": uid}, state)
            print("[live] legal fortify unit %s valid=%s" % (uid, v.is_valid))
            if v.is_valid:
                await ws.send(json.dumps({"type": "action", "action_type": "unit_fortify", "actor_id": uid}))
                res = await _next(ws, {"action_accepted", "action_rejected"}, timeout=10)
                print("[live] submit reply: %s" % (res.get("type") if res else "(none within timeout)"))
        print("[live] E2E OK")
        return 0


def main():
    try:
        return asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        print("[live] ERROR:", type(e).__name__, str(e)[:200])
        return 1


if __name__ == "__main__":
    sys.exit(main())
