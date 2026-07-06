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

Turn advancement (Issue #25): end_turn is now sent as a proper ``action`` message
(``client.action_message`` -> ``{"type":"action","action":{"action_type":"end_turn"}}``) so the
proxy passes validation, emits PACKET_PLAYER_PHASE_DONE, and the server ticks the turn; the loop
then waits for the turn to actually increment (``turncycle.await_turn_advance``) instead of a blind
sleep, and is driven by *observed* turns.

Config (env): FREECIV_PROXY_WS, FREECIV_API_TOKEN, FREECIV_GAME_ID, FREECIV_TURNS,
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

from freeciv import adapter, atoms, actions, client, turncycle  # noqa: E402
import provider_config as pc  # noqa: E402

WS = os.environ.get("FREECIV_PROXY_WS", "ws://localhost:8002/llmsocket/8002")
TOKEN = os.environ.get("FREECIV_API_TOKEN", "test-token-fc3d-001")
GAME = os.environ.get("FREECIV_GAME_ID", "omega_llm")
TURNS = int(os.environ.get("FREECIV_TURNS", os.environ.get("FREECIV_CYCLES", "3")))
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


# State polling + typed-message waiting are shared with live_play via turncycle (Issue #25).
_nxt = turncycle.recv_until
_get_state = turncycle.get_state


def _to_packet(action):
    """Build the proxy action message for a validated action (action-nested agent format)."""
    return client.action_message(actions.normalize_action(action))


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

        totals = {"proposed": 0, "submitted": 0, "blocked": 0, "turns_advanced": 0, "turns_seen": []}
        cur = turncycle.turn_of(st)
        # Drive by OBSERVED turn advances, not a fixed cycle count: re-deciding over a held
        # turn must not count. Cap attempts so a genuinely stuck game still terminates.
        for _ in range(TURNS * 3):
            st = await _get_state(ws) or st
            cur = turncycle.turn_of(st) if st else cur
            norm = adapter.normalize_state(st)
            facts = adapter.facts_from_state(norm)
            sents = atoms.sentences_from_facts(facts)
            mine = [u for u in norm["units"] if u.get("owner") == norm["player_perspective"]]
            print("\n=== turn %s | %d atoms | %d units ===" % (cur, len(sents), len(mine)))
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
            await turncycle.send_end_turn(ws)
            nt = await turncycle.await_turn_advance(ws, cur, timeout=40)
            if nt is None:
                print("   [warn] turn did not advance past %s within timeout" % cur)
                break
            totals["turns_advanced"] += 1
            totals["turns_seen"].append(nt)
            print("   -> advanced to turn %s" % nt)
            if totals["turns_advanced"] >= TURNS:
                break

        print("\n=== LLM-DRIVEN LIVE RUN ===")
        print(json.dumps(totals, indent=2))
        print("illegal actions that reached the game: 0 (all %d blocked pre-submit)" % totals["blocked"])
        print("turns advanced: %d (%s)" % (totals["turns_advanced"], totals["turns_seen"]))
        return 0 if totals["turns_advanced"] >= TURNS else 1


def main():
    try:
        return asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        print("[llm] ERROR:", type(e).__name__, str(e)[:200])
        return 1


if __name__ == "__main__":
    sys.exit(main())
