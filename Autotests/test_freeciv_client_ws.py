"""Regression tests for the FreecivClient live WebSocket submit sequence (Issue #25 follow-up).

The #25 fix corrected the ``action`` envelope, but ``FreecivClient.submit_action()`` — the
foundational ``freeciv-action`` tool path (``freeciv_tool.act`` -> ``submit_action``) — still
(a) sent ``llm_connect`` with ``api_token`` nested under ``data`` (upstream
``freeciv-proxy/message_validator.py:75`` requires it top-level → ``E220`` before any action
is submitted), and (b) targeted the llm-gateway ``/ws/agent/...`` route instead of the
proxy's validated ``/llmsocket/<port>`` endpoint the #25 runners use.

These tests pin the full ``submit_action`` wire sequence: the ``llm_connect`` handshake shape,
the URL contract, and the ordered frames actually sent on the socket. Pure-Python, no live
server (a fake ``websockets`` module is injected). The message contract is mirrored from
upstream ``freeciv-proxy/message_validator.py`` (schemas at :75 llm_connect, :133 action).
"""
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BENCH = os.path.join(_REPO_ROOT, "benchmarks")
for _p in (_BENCH, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from freeciv.client import FreecivClient, action_message, end_turn_message  # noqa: E402

# Upstream required top-level fields (freeciv-proxy/message_validator.py:75 / :133).
_LLM_CONNECT_REQUIRED = ("type", "agent_id", "api_token")
_ACTION_REQUIRED = ("type", "action")


def _client(**over):
    kw = dict(proxy_url="http://h:8002", ws_url="ws://h:8002/llmsocket/8002",
              api_token="test-token-fc3d-001", game_id="test_game", player_id=1,
              agent_id="omegaclaw", civserver_port=6001)
    kw.update(over)
    return FreecivClient(**kw)


def test_llm_connect_has_top_level_api_token():
    cm = _client().connect_message()
    for f in _LLM_CONNECT_REQUIRED:
        assert f in cm, "missing top-level {!r}: {}".format(f, cm)
    assert cm["api_token"] == "test-token-fc3d-001"
    # the regression: api_token must NOT be nested under a 'data' object
    assert "data" not in cm


def test_action_envelopes_satisfy_action_schema():
    am = action_message({"type": "unit_move", "unit_id": 7, "dest_x": 5, "dest_y": 5})
    for f in _ACTION_REQUIRED:
        assert f in am
    assert isinstance(am["action"], dict)
    assert am["action"]["action_type"] == "unit_move" and am["action"]["actor_id"] == 7
    assert end_turn_message() == {"type": "action", "action": {"action_type": "end_turn"}}


def test_actor_id_preserved_when_passed_directly():
    am = action_message({"type": "unit_fortify", "actor_id": 9})
    assert am["action"]["actor_id"] == 9


def test_ws_uri_is_llmsocket_and_not_gateway_route():
    c = _client()
    assert c._ws_uri() == "ws://h:8002/llmsocket/8002"
    assert "/ws/agent/" not in c._ws_uri()


def test_from_env_defaults_to_proxy_llmsocket(monkeypatch=None):
    saved = {k: os.environ.get(k) for k in ("FREECIV_WS_URL", "FREECIV_PROXY_WS")}
    for k in saved:
        os.environ.pop(k, None)
    try:
        assert FreecivClient.from_env()._ws_uri().endswith("/llmsocket/8002")
        os.environ["FREECIV_PROXY_WS"] = "ws://localhost:8002/llmsocket/8002"
        assert FreecivClient.from_env()._ws_uri() == "ws://localhost:8002/llmsocket/8002"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_submit_action_wire_sequence():
    """Drive submit_action() through a fake websockets module; assert exact frames + URL."""
    c = _client()
    sent = []

    class _FakeWS:
        async def send(self, data):
            sent.append(json.loads(data))

        async def recv(self):
            last = sent[-1]["type"]
            return json.dumps({"type": "auth_success" if last == "llm_connect" else "action_ack"})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _FakeConnect:
        def __init__(self, uri):
            self.uri = uri

        async def __aenter__(self):
            _FakeConnect.uri_seen = self.uri
            return _FakeWS()

        async def __aexit__(self, *a):
            return False

    class _FakeWebsockets:
        connect = staticmethod(lambda uri, *a, **k: _FakeConnect(uri))

    saved = sys.modules.get("websockets")
    sys.modules["websockets"] = _FakeWebsockets()
    try:
        replies = c.submit_action({"type": "end_turn"})
    finally:
        if saved is not None:
            sys.modules["websockets"] = saved
        else:
            sys.modules.pop("websockets", None)

    assert _FakeConnect.uri_seen == "ws://h:8002/llmsocket/8002"
    assert [m["type"] for m in sent] == ["llm_connect", "action"]
    assert sent[0]["api_token"] == "test-token-fc3d-001" and "data" not in sent[0]
    assert sent[1] == {"type": "action", "action": {"action_type": "end_turn"}}
    assert len(replies) == 2 and replies[0]["type"] == "auth_success"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok:", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL:", fn.__name__, e)
    if failed:
        sys.exit(1)
    print(f"\nAll {len(fns)} freeciv client ws tests passed")
