"""Shared fixtures for the turn-cycle KPI benchmark + host test (Issue #25).

`MockProxyWS` is an in-process stand-in for the freeciv-proxy `/llmsocket` that models the TWO
gates a turn-advancing message must pass — both verified against the live stack:

1. `message_validator`: the `action` message schema requires a top-level `action` **dict**
   (`freeciv-proxy/message_validator.py:133`, `required_fields: [type, action]`). A bare
   top-level `action_type` — or nesting under `data` — fails with `E220: Missing required
   field: action`. This is the gate the pre-#25 client tripped (stuck on turn 1).
2. `_handle_action` + `_normalize_agent_action`: reads the `action` dict and maps the agent
   format to the internal action; `end_turn` becomes PACKET_PLAYER_PHASE_DONE (advances).

So the mock advances the turn ONLY for `{"type":"action","action":{"action_type":"end_turn"}}`
(or the legacy `{"action":{"type":"end_turn"}}`); the old top-level and `data`-nested shapes
are rejected with E220 and never advance. Async + stdlib only.
"""

import asyncio
import json


class MockProxyWS:
    def __init__(self, start_turn=1):
        self.turn = start_turn
        self._out = asyncio.Queue()
        self.received = []
        self.phase_done_packets = 0  # times a correctly-shaped end_turn advanced the turn
        self.rejected = 0            # messages that failed the E220 action-required gate

    async def send(self, raw):
        msg = json.loads(raw)
        self.received.append(msg)
        mtype = msg.get("type")
        if mtype == "llm_connect":
            await self._out.put({"type": "auth_success", "player_id": 1})
        elif mtype == "state_query":
            await self._out.put({"type": "state_response", "data": self.state()})
        elif mtype == "action":
            # Gate 1: message_validator requires a top-level `action` dict (E220 otherwise).
            action_obj = msg.get("action")
            if not isinstance(action_obj, dict):
                self.rejected += 1
                await self._out.put({"type": "error", "error_code": "E220",
                                     "message": "Missing required field: action"})
                return
            # Gate 2: extract + normalize the agent/legacy form; end_turn advances the phase.
            atype = action_obj.get("action_type") or action_obj.get("type")
            if atype == "end_turn":
                self.turn += 1
                self.phase_done_packets += 1
                await self._out.put({"type": "begin_turn", "turn": self.turn})
            await self._out.put({"type": "action_accepted"})
        elif mtype == "chat":
            pass

    async def recv(self):
        return json.dumps(await self._out.get())

    def state(self):
        return {"turn": self.turn, "phase": "movement", "player_perspective": 1,
                "units": {"7": {"id": 7, "type": "Warrior", "owner": 1, "x": 3, "y": 4, "hp": 10}},
                "cities": {}}


# The two wire shapes under test.
def candidate_end_turn():
    """Correct (#25 fix) envelope built by the shipping helper."""
    from . import client
    return client.end_turn_message()


def baseline_end_turn():
    """The pre-#25 buggy shape: top-level action_type the proxy silently drops."""
    return {"type": "action", "action_type": "end_turn"}


async def drive_turns(end_turn_msg, k, timeout=5):
    """Attempt to advance `k` turns using `end_turn_msg`; return the list of turns observed."""
    from . import turncycle
    ws = MockProxyWS(start_turn=1)
    seen = []
    cur = turncycle.turn_of(await turncycle.get_state(ws))
    for _ in range(k):
        await ws.send(json.dumps(end_turn_msg))
        nt = await turncycle.await_turn_advance(ws, cur, timeout=timeout)
        if nt is None:
            break
        seen.append(nt)
        cur = nt
    return seen
