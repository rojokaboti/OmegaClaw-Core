"""OmegaClaw <-> freeciv-llm live tool shim (Issue #6).

Bridges the deterministic adapter (under ``benchmarks/freeciv/``) to two agent tools:

  - ``freeciv-observe`` -> :func:`observe`: fetch the current game state and return it as
    PLN sentences the agent can reason over.
  - ``freeciv-action``  -> :func:`act`: validate a candidate action against the current
    state and the server's legal_actions, and submit it ONLY if legal. An illegal action is
    refused (logged, structured deny) and never reaches ``action_submit`` — the 0%-illegal
    KPI, enforced at the agent's edge.

The heavy/deterministic logic lives in the benchmark-local package (the "producer stays
benchmark-local" decision); this shim only puts it on ``sys.path`` and wires it to the loop.
Importing this module pulls in no network/websocket deps — those load lazily on live submit.
Self-test runs fully offline (fixture state + a fake client), so it satisfies the CI
Phase-1 ``python3 src/freeciv_tool.py`` self-test convention.
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BENCHMARKS = os.path.join(os.path.dirname(_HERE), "benchmarks")
if _BENCHMARKS not in sys.path:
    sys.path.insert(0, _BENCHMARKS)

from freeciv import adapter, atoms, actions  # noqa: E402
from freeciv import client as _client_mod  # noqa: E402

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = _client_mod.FreecivClient.from_env()
    return _client


def _deny(code, message):
    print("[freeciv] ACTION_DENIED code={} msg={}".format(code, message), flush=True)
    return json.dumps({"status": "denied", "error_code": code, "error_message": message})


def observe(state=None):
    """Return the current game state as newline-joined PLN sentences (a reasoning premise set).

    ``state`` may be supplied (tests / replay); otherwise it is fetched from the live proxy.
    Also seeds a per-game reasoning session with these premises (Issue #8), so subsequent
    ``metta-session-infer`` reuses them across turns without re-sending the whole state.
    """
    if state is None:
        state = _get_client().get_state()
    facts = adapter.facts_from_state(adapter.normalize_state(state))
    sentences = atoms.sentences_from_facts(facts)
    _seed_session(state, sentences)
    return "\n".join(sentences) if sentences else "(no facts)"


def _seed_session(state, sentences):
    """Best-effort: add this turn's premises to the game's reasoning session + snapshot it.

    Keyed on FREECIV_GAME_ID so all turns of a game share one session. Never raises — a
    session/snapshot failure must not break observation.
    """
    try:
        import metta_sessions
        sid = "freeciv:" + os.environ.get("FREECIV_GAME_ID", "freeciv")
        for s in sentences:
            metta_sessions.add_fact(sid, s)
        metta_sessions.snapshot(sid)
    except Exception:  # noqa: BLE001
        pass


def act(action_json, state=None, legal_actions=None):
    """Validate then (only if legal) submit an action. Returns a structured JSON string.

    ``action_json`` is a JSON string (as emitted by the model) or an already-parsed dict.
    """
    try:
        action = json.loads(action_json) if isinstance(action_json, str) else action_json
    except (ValueError, TypeError) as e:
        return _deny("E001", "invalid action JSON: {}".format(e))
    if not isinstance(action, dict):
        return _deny("E001", "action must be a JSON object")

    client = None
    if state is None:
        client = _get_client()
        state = client.get_state()
    if legal_actions is None and client is not None:
        try:
            legal_actions = client.get_legal_actions()
        except _client_mod.FreecivClientError:
            legal_actions = None  # validate on state alone if the proxy can't list them

    result = actions.validate_action(action, state, legal_actions)
    if not result.is_valid:
        return _deny(result.error_code, result.error_message)

    normalized = actions.normalize_action(action)
    try:
        reply = (client or _get_client()).submit_action(normalized)
    except _client_mod.FreecivClientError as e:
        return _deny("E500", "submit failed: {}".format(e))
    return json.dumps({"status": "submitted", "action": normalized, "result": reply})


# --------------------------------------------------------------------------- self-test

def _selftest():
    """Offline self-test: no network. Uses a fixture state + a recording fake client."""
    state = {
        "format": "llm_optimized", "turn": 5, "phase": "movement", "player_perspective": 1,
        "economic": {"resources": {"gold": 10, "science": 2}},
        "players": {"1": {"id": 1, "name": "Rome"}},
        "units": {"7": {"id": 7, "type": "Settler", "owner": 1, "x": 4, "y": 4, "hp": 10},
                  "8": {"id": 8, "type": "Warrior", "owner": 0, "x": 5, "y": 4, "hp": 10}},
        "cities": {}, "techs": {"player1": ["Pottery"]},
    }

    # observe -> deterministic PLN sentences
    out = observe(state=state)
    assert "(stv" in out and "Unit_7" in out, out
    assert observe(state=state) == out, "observe not deterministic"

    class _FakeClient:
        def __init__(self):
            self.submitted = []

        def get_state(self):
            return state

        def get_legal_actions(self):
            return [{"type": "unit_move", "unit_id": 7, "target": {"x": 5, "y": 5}}]

        def submit_action(self, action):
            self.submitted.append(action)
            return {"status": "ok"}

    global _client
    _client = _FakeClient()
    try:
        # legal action -> submitted
        r = json.loads(act(json.dumps({"type": "unit_move", "unit_id": 7, "dest_x": 5, "dest_y": 5})))
        assert r["status"] == "submitted", r
        assert _client.submitted and _client.submitted[0]["type"] == "unit_move", _client.submitted

        # illegal (enemy unit) -> denied, NOT submitted
        before = len(_client.submitted)
        r = json.loads(act(json.dumps({"type": "unit_move", "unit_id": 8, "dest_x": 5, "dest_y": 5})))
        assert r["status"] == "denied" and r["error_code"] == "E202", r
        assert len(_client.submitted) == before, "illegal action must not be submitted"

        # malformed JSON -> denied
        r = json.loads(act("{not json"))
        assert r["status"] == "denied" and r["error_code"] == "E001", r
    finally:
        _client = None

    print("freeciv_tool self-tests passed")


if __name__ == "__main__":
    _selftest()
