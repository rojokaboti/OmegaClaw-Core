"""Turn-cycle / end-turn handshake tests (Issue #25).

Pure-Python, no Docker/LLM. Runs under pytest and standalone
(`python3 Autotests/test_freeciv_turn_cycle.py`).

The game got stuck on turn 1 because the client sent end_turn with a top-level `action_type`,
which the proxy's `message_validator` rejects with `E220` (its `action` schema requires a
top-level `action` dict) — so no PACKET_PLAYER_PHASE_DONE was emitted — AND the loop never
checked that the turn incremented.

A tiny in-process `MockProxyWS` models both proxy gates (the `E220` action-required validator +
the extract/normalize→advance rule) and only advances the turn for a correctly-shaped end_turn.
The tests assert:
  (a) `client.action_message` builds the `action`-nested envelope the proxy accepts;
  (b) `turncycle.await_turn_advance` detects the increment and drives >=3 turns;
  (c) the OLD top-level-`action_type` shape AND the `data`-nested shape do NOT advance (E220 regression guard).
"""
import asyncio
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BENCH = os.path.join(_REPO_ROOT, "benchmarks")
for _p in (_BENCH, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from freeciv import client, turncycle  # noqa: E402
from freeciv_turn_cycle_fixtures import MockProxyWS  # noqa: E402  (shared with the benchmark)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- envelope ---------------------------------------------------------------

def test_end_turn_envelope_nests_under_action():
    msg = client.end_turn_message()
    # live-verified shape: message_validator requires a top-level `action` dict
    assert msg == {"type": "action", "action": {"action_type": "end_turn"}}
    # NOT the broken top-level shape (E220) nor the data-nested shape (also E220)
    assert "action_type" not in msg and "data" not in msg


def test_unit_action_envelope_uses_actor_id_under_action():
    msg = client.action_message({"type": "unit_fortify", "unit_id": 7})
    assert msg["type"] == "action"
    assert msg["action"] == {"action_type": "unit_fortify", "actor_id": 7}
    mv = client.action_message({"type": "unit_move", "unit_id": 7, "dest_x": 4, "dest_y": 5})
    assert mv["action"] == {"action_type": "unit_move", "actor_id": 7, "dest_x": 4, "dest_y": 5}


# --- advancement (correct envelope) ----------------------------------------

def test_await_turn_advance_detects_increment():
    async def go():
        ws = MockProxyWS(start_turn=1)
        await turncycle.send_end_turn(ws)
        return await turncycle.await_turn_advance(ws, prev_turn=1, timeout=5)
    assert _run(go()) == 2


def test_drives_at_least_three_monotonic_turns():
    async def go():
        ws = MockProxyWS(start_turn=1)
        seen = []
        cur = turncycle.turn_of(await turncycle.get_state(ws))
        for _ in range(3):
            await turncycle.send_end_turn(ws)
            nt = await turncycle.await_turn_advance(ws, cur, timeout=5)
            assert nt is not None
            seen.append(nt)
            cur = nt
        return seen, ws.phase_done_packets
    seen, packets = _run(go())
    assert seen == [2, 3, 4], seen              # monotonic 1 -> 2 -> 3 -> 4
    assert packets == 3                          # one phase-done per turn


# --- regression: the OLD broken shape must NOT advance ----------------------

def test_old_top_level_shape_does_not_advance():
    async def go():
        ws = MockProxyWS(start_turn=1)
        # exactly what the buggy client sent before Issue #25
        await ws.send(json.dumps({"type": "action", "action_type": "end_turn"}))
        advanced = await turncycle.await_turn_advance(ws, prev_turn=1, timeout=2)
        return advanced, ws.phase_done_packets, ws.turn, ws.rejected
    advanced, packets, turn, rejected = _run(go())
    assert advanced is None      # never advanced
    assert packets == 0          # no phase-done packet was triggered
    assert turn == 1             # stuck on turn 1, reproducing the bug
    assert rejected == 1         # rejected by the E220 action-required gate


def test_data_nested_shape_also_rejected():
    # the intermediate `data` envelope ALSO fails the message_validator (E220), confirmed live.
    async def go():
        ws = MockProxyWS(start_turn=1)
        await ws.send(json.dumps({"type": "action", "data": {"action_type": "end_turn"}}))
        advanced = await turncycle.await_turn_advance(ws, prev_turn=1, timeout=2)
        return advanced, ws.turn, ws.rejected
    advanced, turn, rejected = _run(go())
    assert advanced is None and turn == 1 and rejected == 1


def test_legacy_action_nested_shape_also_advances():
    # the proxy also accepts the legacy {"type":"action","action":{"type":"end_turn"}}.
    async def go():
        ws = MockProxyWS(start_turn=1)
        await ws.send(json.dumps({"type": "action", "action": {"type": "end_turn"}}))
        return await turncycle.await_turn_advance(ws, prev_turn=1, timeout=5)
    assert _run(go()) == 2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("\nAll {} freeciv turn-cycle tests passed".format(len(fns)))
