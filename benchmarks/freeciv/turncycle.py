"""Shared turn-cycle helpers for the live freeciv runners (Issue #25).

Centralizes the pieces that used to be duplicated (and divergent) across ``llm_play.py`` /
``live_play.py``: typed message waiting, ``llm_optimized`` state polling, and — the point of
Issue #25 — **turn-advance detection**. The action envelope itself lives in
``client.action_message`` (the single wire-dialect source). Async + stdlib only; the live
``websockets`` dependency belongs to the caller.

Why this exists: the game got stuck on turn 1 because end_turn was sent with the wrong
envelope (dropped by the proxy) AND the runners never checked that the turn actually
incremented (blind ``sleep`` + fixed cycle count). :func:`await_turn_advance` closes the
second half: it drives the loop by *observed* turns, not by attempts.
"""

import asyncio
import json

from . import client

# Re-export so callers have one import for the whole turn-cycle surface.
action_message = client.action_message
end_turn_message = client.end_turn_message

_STATE_TYPES = {"state_response", "state_update"}
# Server pushes that announce a new turn/phase (best-effort; polling is the reliable path).
TURN_PUSH_TYPES = {"begin_turn", "turn_begin", "new_turn", "phase_change"}


async def recv_until(ws, types, timeout=20, drain=500):
    """Return the first received message whose ``type`` is in ``types`` (or None on timeout)."""
    for _ in range(drain):
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        except asyncio.TimeoutError:
            return None
        if isinstance(m, dict) and m.get("type") in types:
            return m
    return None


def state_body(m):
    """Unwrap a state message: prefer ``data`` when it carries the ``turn`` field."""
    if not m:
        return None
    d = m.get("data")
    return d if isinstance(d, dict) and "turn" in d else m


async def get_state(ws, fmt="llm_optimized"):
    """Query and return the current ``llm_optimized`` state dict (or None)."""
    await ws.send(json.dumps({"type": "state_query", "format": fmt}))
    return state_body(await recv_until(ws, _STATE_TYPES, timeout=15))


def turn_of(state):
    """Integer turn from a state dict, or None if absent/unparseable."""
    if not state or state.get("turn") is None:
        return None
    try:
        return int(state.get("turn"))
    except (TypeError, ValueError):
        return None


async def send_end_turn(ws):
    """Send the correctly-shaped end_turn action (advances the phase via pid 52)."""
    await ws.send(json.dumps(end_turn_message()))


async def await_turn_advance(ws, prev_turn, timeout=40, poll=1.0):
    """Poll state until the turn strictly advances past ``prev_turn``.

    Returns the new (larger) turn number, or None if it did not advance within ``timeout``
    seconds. ``prev_turn`` None means "accept the first integer turn observed" (used once to
    latch the starting turn). This is the detection the old runners lacked — the loop should
    count *observed* advances, not attempts, so re-deciding over a held turn never counts.
    """
    attempts = max(1, int(timeout / poll))
    for _ in range(attempts):
        st = await get_state(ws)
        t = turn_of(st)
        if t is not None and (prev_turn is None or t > prev_turn):
            return t
        await asyncio.sleep(poll)
    return None
