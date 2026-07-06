"""Live E2E runner against a running taso-ventures/freeciv-llm stack (Issues #6 + #25).

Connects to the freeciv-proxy LLM socket, ensures a game is started, pulls a real
`llm_optimized` state, converts it to deterministic PLN atoms, blocks an illegal move
pre-submit, then **drives >=3 real turns** (Issue #25): each turn submit a trivial legal
action (unit_fortify) via the correct `action` envelope, send end_turn, and wait for the turn
to actually increment. Needs NO LLM/provider key — it is the deterministic acceptance runner
for turn advancement. `llm_play.py` is the LLM-in-the-loop variant.

This is a demo/validation runner, not part of the host test suite (it needs the live stack
and the `websockets` package). The deterministic adapter it exercises and the turn-advance
logic are host-tested (`Autotests/test_freeciv_adapter.py`, `Autotests/test_freeciv_turn_cycle.py`).

Game-start note: freeciv-llm civservers default to `minplayers=2`, so a single agent + aifill
never leaves pregame (turn 0, no units). While in pregame this runner issues
`/set minplayers 1` then `/start` (server commands via the `chat` message type).

Config (env): FREECIV_PROXY_WS (default ws://localhost:8002/llmsocket/8002),
FREECIV_API_TOKEN (default test-token-fc3d-001), FREECIV_AGENT_ID, FREECIV_GAME_ID,
FREECIV_TURNS (default 3). Run: python3 benchmarks/freeciv/live_play.py
"""
import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BENCH = os.path.dirname(_HERE)
if _BENCH not in sys.path:
    sys.path.insert(0, _BENCH)

from freeciv import adapter, atoms, actions, client, turncycle  # noqa: E402

WS_URL = os.environ.get("FREECIV_PROXY_WS", "ws://localhost:8002/llmsocket/8002")
TOKEN = os.environ.get("FREECIV_API_TOKEN", "test-token-fc3d-001")
AGENT = os.environ.get("FREECIV_AGENT_ID", "omega")
GAME = os.environ.get("FREECIV_GAME_ID", "omega_e2e")
TURNS = int(os.environ.get("FREECIV_TURNS", "3"))

# State polling + typed-message waiting shared with llm_play via turncycle (Issue #25).
_next = turncycle.recv_until
_get_state = turncycle.get_state


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

        illegal = {"type": "unit_move", "unit_id": 99999, "dest_x": 1, "dest_y": 1}
        vb = actions.validate_action(illegal, state)
        print("[live] illegal unknown-unit blocked pre-submit: is_valid=%s code=%s" % (vb.is_valid, vb.error_code))

        # --- Issue #25: drive >=TURNS real turns, asserting monotonic advancement -----------
        turns_seen = []
        cur = turncycle.turn_of(state)
        for _ in range(TURNS * 3):  # attempt cap so a stuck game still terminates
            state = await _get_state(ws) or state
            cur = turncycle.turn_of(state) if state else cur
            norm = adapter.normalize_state(state)
            mine = [u for u in norm["units"] if u.get("owner") == norm["player_perspective"]]
            if mine:  # submit a trivial legal action so the turn does real work
                uid = mine[0]["id"]
                if actions.validate_action({"type": "unit_fortify", "unit_id": uid}, state).is_valid:
                    await ws.send(json.dumps(client.action_message({"type": "unit_fortify", "unit_id": uid})))
            await turncycle.send_end_turn(ws)
            nt = await turncycle.await_turn_advance(ws, cur, timeout=40)
            if nt is None:
                print("[live] turn did NOT advance past %s within timeout" % cur)
                break
            turns_seen.append(nt)
            print("[live] turn %s -> %s" % (cur, nt))
            if len(turns_seen) >= TURNS:
                break

        ok = len(turns_seen) >= TURNS and turns_seen == sorted(set(turns_seen)) and len(set(turns_seen)) == len(turns_seen)
        print("[live] turns advanced: %d (%s) monotonic=%s" % (len(turns_seen), turns_seen, ok))
        print("[live] E2E %s" % ("OK" if ok else "INCOMPLETE"))
        return 0 if ok else 1


def main():
    try:
        return asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        print("[live] ERROR:", type(e).__name__, str(e)[:200])
        return 1


if __name__ == "__main__":
    sys.exit(main())
