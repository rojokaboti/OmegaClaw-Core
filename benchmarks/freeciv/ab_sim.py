"""One A/B arm: drive a live FreeCiv game with the shared LLM, logging per-turn metrics (Issue #25).

Two arms, identical except the state rendering the LLM sees:
  --arm plain : LLM over a plain structured summary (no PLN, no reasoning).
  --arm pln   : the same summary PLUS authentic MeTTa/PLN-derived recommendations (reason.derive).

Both use the same provider/model (env, via llm_agent), the same action schema, the same pre-submit
validate_action, the same seeded map, and the #25 turn-advance handshake. Per turn we append one
JSONL metrics record and refresh a heartbeat so the 30-min reporter can see liveness.

Run (in-container): python3 benchmarks/freeciv/ab_sim.py --arm pln --game-id g --seed 42 \
    --hours 10 --max-turns 2000 --out benchmarks/freeciv/ab_runs/<ts>
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

from freeciv import adapter, atoms, actions, client, turncycle, metrics, llm_agent, reason, duel_sim  # noqa: E402

WS = os.environ.get("FREECIV_PROXY_WS", "ws://localhost:8002/llmsocket/8002")
TOKEN = os.environ.get("FREECIV_API_TOKEN", "test-token-fc3d-001")


def _log(out_dir, arm, record):
    record["ts"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    record["arm"] = arm
    with open(os.path.join(out_dir, "%s.jsonl" % arm), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _heartbeat(out_dir, arm, **kv):
    kv["ts"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    kv["arm"] = arm
    with open(os.path.join(out_dir, "%s.heartbeat" % arm), "w", encoding="utf-8") as f:
        json.dump(kv, f)


async def _connect(ws_mod, arm):
    ws = await ws_mod.connect(WS, open_timeout=30, max_size=None, ping_interval=None)
    await ws.send(json.dumps({"type": "llm_connect", "agent_id": "omega-%s" % arm, "api_token": TOKEN,
                              "game_id": os.environ["FREECIV_GAME_ID"], "nation": "Romans",
                              "leader_name": "Caesar"}))
    await turncycle.recv_until(ws, {"auth_success"}, timeout=40)
    return ws


async def _pregame(ws, seed):
    """Seeded, identical start for both arms; wait for a populated state."""
    st = await turncycle.get_state(ws)
    if st and st.get("units"):
        return st
    for cmd in ("/set mapseed %d" % seed, "/set gameseed %d" % seed,
                "/set minplayers 1", "/set aifill 3", "/start"):
        await ws.send(json.dumps({"type": "chat", "message": cmd}))
        await asyncio.sleep(1.0)
    for _ in range(25):
        await asyncio.sleep(4)
        st = await turncycle.get_state(ws)
        if st and st.get("units"):
            return st
    return st


def _context(arm, norm):
    """Arm-specific state rendering + (pln) reasoning. Returns (context_text, reason_ms, recs).

    ``recs`` is the raw derived-recommendation list (empty for the plain arm) — the caller uses it
    both for the conclusion count and to flag which per-unit moves PLN recommended.
    """
    plain = llm_agent.render_plain(norm)
    if arm != "pln":
        return plain, None, []
    facts = atoms.sentences_from_facts(adapter.facts_from_state(norm))
    t0 = time.time()
    recs = reason.derive(facts)
    reason_ms = int((time.time() - t0) * 1000)
    block = reason.format_for_llm(recs)
    return (plain + ("\n\n" + block if block else "")), reason_ms, recs


async def run(arm, seed, hours, max_turns, out_dir):
    import websockets  # live-only dependency
    if not llm_agent.have_key():
        print("[%s] provider key missing (%s)" % (arm, llm_agent.provider_info()), flush=True)
        return 2
    print("[%s] provider=%s" % (arm, llm_agent.provider_info()), flush=True)
    deadline = time.time() + hours * 3600.0
    totals = {"proposed": 0, "submitted": 0, "blocked": 0, "turns_advanced": 0,
              "llm_errors": 0, "reconnects": 0}
    turns_seen = []
    stalls = 0  # consecutive no-advance turns; a persistent plateau ends the game (else it would
                # no_advance-loop until the hours cap when the game has effectively ended)
    ws = None
    try:
        while time.time() < deadline and totals["turns_advanced"] < max_turns:
            try:
                if ws is None:
                    ws = await _connect(websockets, arm)
                    st = await _pregame(ws, seed)
                    if not st or not st.get("units"):
                        _log(out_dir, arm, {"event": "no_populated_state"})
                        await asyncio.sleep(10); continue
                st = await turncycle.get_state(ws) or st
                norm = adapter.normalize_state(st)
                cur = turncycle.turn_of(st)
                ctx, reason_ms, recs = _context(arm, norm)
                n_conc = len(recs)
                recommendations, rec_ents = duel_sim._parse_recs(recs)
                mine = [u for u in norm["units"] if u.get("owner") == norm["player_perspective"]]
                acts, meta = llm_agent.decide(ctx, mine)
                proposed = submitted = blocked = 0
                moves = []
                for a in acts:
                    proposed += 1
                    na = actions.normalize_action(a)
                    valid = actions.validate_action(a, st).is_valid
                    if valid:
                        await ws.send(json.dumps(client.action_message(na)))
                        submitted += 1
                    else:
                        blocked += 1
                    moves.append(duel_sim._move_record(na, valid, rec_ents))
                await turncycle.send_end_turn(ws)
                nt = await turncycle.await_turn_advance(ws, cur, timeout=45)
                if meta.get("error"):
                    totals["llm_errors"] += 1
                totals["proposed"] += proposed; totals["submitted"] += submitted; totals["blocked"] += blocked
                rec = {"turn": cur, "advanced_to": nt, "metrics": metrics.metrics_from_state(norm),
                       "proposed": proposed, "submitted": submitted, "blocked": blocked,
                       "illegal_rate": (blocked / proposed) if proposed else 0.0,
                       "llm_ms": meta.get("llm_ms"), "reason_ms": reason_ms, "n_conclusions": n_conc,
                       "prompt_chars": meta.get("prompt_chars"), "llm_error": meta.get("error"),
                       "moves": moves, "recommendations": recommendations}
                _log(out_dir, arm, rec)
                if nt is not None:
                    totals["turns_advanced"] += 1; turns_seen.append(nt); stalls = 0
                _heartbeat(out_dir, arm, turn=(nt if nt is not None else cur), **totals)
                if nt is None:
                    stalls += 1
                    _log(out_dir, arm, {"event": "no_advance", "at_turn": cur, "stalls": stalls})
                    if stalls >= 8:  # game has effectively ended/plateaued — stop, don't burn to the cap
                        _log(out_dir, arm, {"event": "plateau_end", "at_turn": cur}); break
                    await asyncio.sleep(5)
            except (websockets.ConnectionClosed, OSError) as e:  # reconnect + continue
                totals["reconnects"] += 1
                _log(out_dir, arm, {"event": "reconnect", "reason": str(e)[:200]})
                try:
                    if ws is not None:
                        await ws.close()
                except Exception:  # noqa: BLE001
                    pass
                ws = None
                await asyncio.sleep(5)
    finally:
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
    summary = {"arm": arm, "seed": seed, "turns_advanced": totals["turns_advanced"],
               "turns_seen": turns_seen, "totals": totals,
               "ended": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
    with open(os.path.join(out_dir, "%s_summary.json" % arm), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("[%s] done: %d turns advanced %s" % (arm, totals["turns_advanced"], turns_seen[:20]), flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["pln", "plain"], required=True)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--hours", type=float, default=10.0)
    ap.add_argument("--max-turns", type=int, default=2000)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.environ["FREECIV_GAME_ID"] = args.game_id
    os.makedirs(args.out, exist_ok=True)
    try:
        return asyncio.run(run(args.arm, args.seed, args.hours, args.max_turns, args.out))
    except Exception as e:  # noqa: BLE001
        print("[%s] FATAL: %s: %s" % (args.arm, type(e).__name__, str(e)[:300]), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
