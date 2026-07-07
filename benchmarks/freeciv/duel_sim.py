"""Head-to-head 1v1: PLN (OmegaClaw) vs plain-LLM as opposing players in ONE FreeCiv game.

Both agents connect to the same game_id as two human players and directly compete (no AI:
aifill 0, small map so they contact early). Same model/schema/validation for both; the only
difference is that the PLN side's prompt carries MeTTa/PLN-derived recommendations (reason.derive).

FreeCiv advances a turn only when ALL human players end their phase, so each turn we drive both
sides (observe -> decide -> validate+submit -> end phase) then await the tick. Winner is decided by
elimination (a side with 0 cities AND 0 units is dead) else by territory at the end (cities, then
units, then techs). No score metric needed.

`--pln-side {0|1}` picks which player slot is PLN (the mirror-pair wrapper runs both to control for
start-position bias). Run in-container. See duel_run.sh.
"""

import argparse
import asyncio
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BENCH = os.path.dirname(_HERE)
if _BENCH not in sys.path:
    sys.path.insert(0, _BENCH)

from freeciv import adapter, atoms, actions, client, turncycle, metrics, llm_agent, reason  # noqa: E402

WS = os.environ.get("FREECIV_PROXY_WS", "ws://localhost:8002/llmsocket/8002")
TOKEN = os.environ.get("FREECIV_API_TOKEN", "test-token-fc3d-001")


def _log(out_dir, record):
    record["ts"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    with open(os.path.join(out_dir, "duel.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _heartbeat(out_dir, **kv):
    kv["ts"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    with open(os.path.join(out_dir, "duel.heartbeat"), "w", encoding="utf-8") as f:
        json.dump(kv, f)


async def _connect(ws_mod, agent_id, game_id):
    ws = await ws_mod.connect(WS, open_timeout=30, max_size=None, ping_interval=None)
    await ws.send(json.dumps({"type": "llm_connect", "agent_id": agent_id, "api_token": TOKEN,
                              "game_id": game_id, "nation": "Romans", "leader_name": agent_id}))
    auth = await turncycle.recv_until(ws, {"auth_success"}, timeout=40)
    return ws, (auth.get("player_id") if auth else None)


async def _play_side(ws, is_pln):
    """One side's turn: observe -> (pln: derive) -> decide -> validate+submit -> end phase.
    Returns a per-side metrics record (does NOT wait for the turn to advance)."""
    st = await turncycle.get_state(ws)
    if not st:
        return {"alive": False, "metrics": None, "proposed": 0, "submitted": 0, "blocked": 0,
                "reason_ms": None, "n_conclusions": 0, "llm_ms": None, "error": "no_state"}
    norm = adapter.normalize_state(st)
    m = metrics.metrics_from_state(norm)
    mine = [u for u in norm["units"] if u.get("owner") == norm["player_perspective"]]
    plain = llm_agent.render_plain(norm)
    reason_ms, n_conc = None, 0
    ctx = plain
    if is_pln:
        facts = atoms.sentences_from_facts(adapter.facts_from_state(norm))
        t0 = time.time(); recs = reason.derive(facts); reason_ms = int((time.time() - t0) * 1000)
        n_conc = len(recs)
        block = reason.format_for_llm(recs)
        ctx = plain + ("\n\n" + block if block else "")
    acts, meta = llm_agent.decide(ctx, mine)
    proposed = submitted = blocked = 0
    for a in acts:
        proposed += 1
        if actions.validate_action(a, st).is_valid:
            await ws.send(json.dumps(client.action_message(actions.normalize_action(a))))
            submitted += 1
        else:
            blocked += 1
    await turncycle.send_end_turn(ws)
    # a side is "alive" while it still has a city or a unit
    alive = (m["n_cities"] or 0) > 0 or (m["n_units"] or 0) > 0
    return {"alive": alive, "metrics": m, "proposed": proposed, "submitted": submitted,
            "blocked": blocked, "reason_ms": reason_ms, "n_conclusions": n_conc,
            "llm_ms": meta.get("llm_ms"), "error": meta.get("error")}


def _winner(sideinfo):
    """Decide winner from the latest per-side records: elimination first, then territory."""
    a, b = sideinfo["0"], sideinfo["1"]
    alive0, alive1 = a.get("alive"), b.get("alive")
    if alive0 and not alive1:
        return "0"
    if alive1 and not alive0:
        return "1"
    m0 = a.get("metrics") or {}; m1 = b.get("metrics") or {}
    for k in ("n_cities", "n_units", "n_techs"):
        if (m0.get(k) or 0) != (m1.get(k) or 0):
            return "0" if (m0.get(k) or 0) > (m1.get(k) or 0) else "1"
    return "tie"


async def run(game_id, seed, pln_side, hours, max_turns, out_dir, size):
    import websockets  # live-only dependency
    if not llm_agent.have_key():
        print("[duel] provider key missing (%s)" % llm_agent.provider_info()); return 2
    roles = {str(pln_side): "pln", str(1 - pln_side): "plain"}
    print("[duel] game=%s seed=%s roles(player->arm)=%s" % (game_id, seed, roles), flush=True)
    ws0, ws1 = None, None
    try:
        ws0, pid0 = await _connect(websockets, "duel-A", game_id)
        ws1, pid1 = await _connect(websockets, "duel-B", game_id)
        print("[duel] connected: A player_id=%s, B player_id=%s" % (pid0, pid1), flush=True)
        # Pregame (pure 1v1, small map, seeded). With only human players the server won't start
        # until BOTH players are READY, so we send player_ready on both connections before /start
        # (this is the gate that leaves the game stuck at turn 0 otherwise).
        st = await turncycle.get_state(ws0)
        if not st or not st.get("units"):
            for cmd in ("/set aifill 0", "/set minplayers 2", "/set size %d" % size,
                        "/set mapseed %d" % seed, "/set gameseed %d" % seed):
                await ws0.send(json.dumps({"type": "chat", "message": cmd}))
                await asyncio.sleep(1.0)
            for ws in (ws0, ws1):
                await ws.send(json.dumps({"type": "player_ready"}))
                await asyncio.sleep(1.0)
            await ws0.send(json.dumps({"type": "chat", "message": "/start"}))
            for _ in range(30):
                await asyncio.sleep(4)
                st = await turncycle.get_state(ws0)
                if st and st.get("units"):
                    break
        if not st or not st.get("units"):
            _log(out_dir, {"event": "no_populated_state"}); print("[duel] no populated state"); return 1

        # map connection -> arm: ws0 is player pid0. We label sides by connection A=0, B=1,
        # but which is PLN is set by pln_side over the CONNECTION index (0=A,1=B).
        conns = {0: (ws0, roles["0"] == "pln"), 1: (ws1, roles["1"] == "pln")}
        deadline = time.time() + hours * 3600.0
        turns_played = 0
        last = {"0": {}, "1": {}}
        stalls = 0
        while time.time() < deadline and turns_played < max_turns:
            cur = turncycle.turn_of(await turncycle.get_state(ws0))
            for idx in (0, 1):
                ws, is_pln = conns[idx]
                last[str(idx)] = await _play_side(ws, is_pln)
            nt = await turncycle.await_turn_advance(ws0, cur, timeout=60)
            rec = {"turn": cur, "advanced_to": nt,
                   "side0": {"arm": roles["0"], **{k: last["0"].get(k) for k in
                             ("metrics", "proposed", "submitted", "blocked", "reason_ms",
                              "n_conclusions", "llm_ms", "alive", "error")}},
                   "side1": {"arm": roles["1"], **{k: last["1"].get(k) for k in
                             ("metrics", "proposed", "submitted", "blocked", "reason_ms",
                              "n_conclusions", "llm_ms", "alive", "error")}}}
            _log(out_dir, rec)
            _heartbeat(out_dir, turn=(nt if nt is not None else cur), turns_played=turns_played,
                       pln_side=pln_side)
            # elimination -> game decided
            if not (last["0"].get("alive") and last["1"].get("alive")):
                _log(out_dir, {"event": "elimination", "winner_side": _winner(last), "at_turn": cur})
                break
            if nt is None:
                stalls += 1
                _log(out_dir, {"event": "no_advance", "at_turn": cur, "stalls": stalls})
                if stalls >= 5:  # persistent plateau -> end + judge territory
                    _log(out_dir, {"event": "plateau_end", "at_turn": cur}); break
                await asyncio.sleep(5)
            else:
                stalls = 0; turns_played += 1
    finally:
        for ws in (ws0, ws1):
            if ws is not None:
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
    win = _winner(last)
    summary = {"game_id": game_id, "seed": seed, "pln_side": pln_side, "roles": roles,
               "turns_played": turns_played, "winner_side": win,
               "winner_arm": (roles.get(win) if win in roles else "tie"),
               "final": {"side0": last["0"].get("metrics"), "side1": last["1"].get("metrics")},
               "ended": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
    with open(os.path.join(out_dir, "duel_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("[duel] WINNER: %s (%s) after %d turns | final %s" %
          (summary["winner_arm"], win, turns_played, summary["final"]), flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--pln-side", type=int, choices=[0, 1], default=0)
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--max-turns", type=int, default=5000)
    ap.add_argument("--size", type=int, default=2)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    try:
        return asyncio.run(run(args.game_id, args.seed, args.pln_side, args.hours,
                               args.max_turns, args.out, args.size))
    except Exception as e:  # noqa: BLE001
        print("[duel] FATAL: %s: %s" % (type(e).__name__, str(e)[:300]), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
