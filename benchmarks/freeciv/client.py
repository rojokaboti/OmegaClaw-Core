"""Thin client for the ``taso-ventures/freeciv-llm`` service.

Two surfaces of the **freeciv-proxy** (see freeciv-proxy/API_DOCUMENTATION.md):
  - HTTP (``:8002``): ``get_state`` / ``get_legal_actions`` (stdlib urllib — no deps).
  - WebSocket (``/llmsocket/<proxy_port>`` on ``:8002``): ``submit_action`` sends an
    ``llm_connect`` handshake then an ``action`` message. WebSocket support is imported
    lazily (``websockets``) so importing this module — and the whole deterministic adapter —
    needs no extra packages on the host. The WS dependency is only touched on the live
    submission path (Docker phase).

**Wire contract (verified against upstream ``freeciv-proxy/message_validator.py`` and the
Issue #25-validated runners ``live_play.py`` / ``llm_play.py``):**
  - The WS endpoint is the proxy's ``/llmsocket/<proxy_port>`` handler (registered at
    ``freeciv-proxy/freeciv-proxy.py:319``), NOT the separate llm-gateway ``/ws/agent/...``
    FastAPI route — the ``action`` envelope this client builds is only understood by the
    proxy handler. ``FREECIV_WS_URL`` is therefore the **complete** WS endpoint URL (same
    semantics as the runners' ``FREECIV_PROXY_WS``); it is used verbatim, never suffixed.
  - ``llm_connect`` requires ``type``/``agent_id``/``api_token`` at the **top level**
    (schema ``message_validator.py:75``). Nesting ``api_token`` under ``data`` fails with
    ``E220: Missing required field: api_token`` before any action can be submitted — the
    bug this module previously shipped.
  - The follow-up ``action`` envelope is ``{"type":"action","action":{...}}`` (Issue #25),
    built once by :func:`action_message`.

All endpoints/credentials come from the environment so nothing is hardcoded into OmegaClaw:
  FREECIV_PROXY_URL, FREECIV_WS_URL (or FREECIV_PROXY_WS), FREECIV_API_TOKEN, FREECIV_GAME_ID,
  FREECIV_PLAYER_ID, FREECIV_AGENT_ID, FREECIV_CIVSERVER_PORT.
"""

import json
import os
import urllib.request


class FreecivClientError(Exception):
    pass


# Fields copied into the action payload, with the unit_id -> actor_id rename the proxy's
# agent format uses (freeciv-proxy/llm_handler.py:_normalize_agent_action).
_ACTION_FIELD_KEYS = ("unit_id", "dest_x", "dest_y", "city_id", "production_type", "tech_id", "name")


def action_message(action):
    """Build the freeciv-proxy ``action`` message envelope (Issue #25).

    Two proxy gates constrain the shape (both verified against the live stack):

    1. ``message_validator`` requires the ``action`` message to carry a top-level ``action``
       **dict** (schema ``required_fields: [type, action]``). A bare top-level ``action_type``
       — or nesting under ``data`` — fails with ``E220: Missing required field: action``, so no
       packet is ever produced and the turn is stuck on 1 (the Issue #25 bug).
    2. ``_handle_action`` then reads that dict, and ``_normalize_agent_action`` maps the agent
       format (``action_type`` + ``actor_id``) to the internal action, which becomes
       ``PACKET_PLAYER_PHASE_DONE`` (pid 52) for end_turn.

    So the correct, live-verified envelope is
    ``{"type":"action","action":{"action_type":..,<fields>}}``. This is the single place the
    envelope is built so the runners and ``submit_action`` can never drift into divergent wire
    dialects again.
    """
    atype = action.get("type") or action.get("action_type")
    inner = {"action_type": atype}
    for k in _ACTION_FIELD_KEYS:
        v = action.get(k)
        if v is not None:
            inner["actor_id" if k == "unit_id" else k] = v
    # Preserve an already-server-shaped actor_id when no unit_id was given (callers that
    # pass proxy-native actions), without letting it override an explicit unit_id mapping.
    if "actor_id" not in inner and action.get("actor_id") is not None:
        inner["actor_id"] = action["actor_id"]
    return {"type": "action", "action": inner}


def end_turn_message():
    """The turn-advancing action message: ``{"type":"action","action":{"action_type":"end_turn"}}``."""
    return action_message({"type": "end_turn"})


class FreecivClient:
    def __init__(self, proxy_url, ws_url, api_token, game_id, player_id,
                 agent_id="omegaclaw", civserver_port=6001, timeout=15):
        self.proxy_url = proxy_url.rstrip("/")
        self.ws_url = ws_url.rstrip("/")
        self.api_token = api_token
        self.game_id = game_id
        self.player_id = int(player_id)
        self.agent_id = agent_id
        self.civserver_port = int(civserver_port)
        self.timeout = timeout

    @classmethod
    def from_env(cls):
        # FREECIV_WS_URL (or the runners' FREECIV_PROXY_WS) is the COMPLETE proxy WS
        # endpoint. Default targets the proxy's /llmsocket/<port> handler — the same
        # validated path live_play.py/llm_play.py use — not the llm-gateway /ws/agent route.
        ws_url = (os.environ.get("FREECIV_WS_URL")
                  or os.environ.get("FREECIV_PROXY_WS")
                  or "ws://host.docker.internal:8002/llmsocket/8002")
        return cls(
            proxy_url=os.environ.get("FREECIV_PROXY_URL", "http://host.docker.internal:8002"),
            ws_url=ws_url,
            api_token=os.environ.get("FREECIV_API_TOKEN", "test-token-fc3d-001"),
            game_id=os.environ.get("FREECIV_GAME_ID", "test_game"),
            player_id=os.environ.get("FREECIV_PLAYER_ID", "1"),
            agent_id=os.environ.get("FREECIV_AGENT_ID", "omegaclaw"),
            civserver_port=os.environ.get("FREECIV_CIVSERVER_PORT", "6001"),
        )

    # ---- HTTP (proxy :8002) ----------------------------------------------------------
    def get_state(self, fmt="llm_optimized"):
        url = "{}/api/game/{}/state?player_id={}&format={}".format(
            self.proxy_url, self.game_id, self.player_id, fmt)
        return self._http_get_json(url)

    def get_legal_actions(self):
        url = "{}/api/game/{}/legal_actions?player_id={}".format(
            self.proxy_url, self.game_id, self.player_id)
        return self._http_get_json(url)

    def _http_get_json(self, url):
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - surface a single typed error to the tool layer
            raise FreecivClientError("GET {} failed: {}".format(url, e))

    # ---- WS (proxy /llmsocket/<port> on :8002) ---------------------------------------
    def connect_message(self):
        """The ``llm_connect`` handshake with ``api_token`` at the **top level**.

        The proxy's ``message_validator`` (schema ``message_validator.py:75``) requires
        ``type``/``agent_id``/``api_token`` at the top level; ``game_id``/``port`` are
        optional. Nesting ``api_token`` under ``data`` fails ``E220`` before any action is
        submitted, which is why this is built here (never inlined with a nested shape).
        """
        msg = {"type": "llm_connect", "agent_id": self.agent_id, "api_token": self.api_token}
        if self.game_id:
            msg["game_id"] = self.game_id
        if self.civserver_port:
            msg["port"] = self.civserver_port
        return msg

    def _ws_uri(self):
        """The complete proxy WS endpoint. Used verbatim — never suffixed with an
        ``/ws/agent/...`` path (that is the separate llm-gateway route, which does not
        understand this client's ``action`` envelope)."""
        return self.ws_url

    def submit_action(self, action):
        """Submit a (normalized) action: ``llm_connect`` then an ``action`` message. Returns the replies.

        Uses :func:`connect_message` (top-level ``api_token``, or the proxy rejects the
        handshake with ``E220``) then :func:`action_message` — the proxy dispatch only
        recognizes ``type:"action"`` (``llm_handler.py:387-406``); the old
        ``type:"action_submit"`` fell through to the raw-forward path and was silently
        dropped (Issue #25).
        """
        messages = [
            self.connect_message(),
            action_message(action),
        ]
        return self._ws_roundtrip(messages)

    def _ws_roundtrip(self, messages):
        try:
            import asyncio
            import websockets  # live-only dependency
        except ImportError as e:
            raise FreecivClientError(
                "websocket submit requires the 'websockets' package (live/in-container only): {}".format(e))

        uri = self._ws_uri()

        async def _run():
            replies = []
            async with websockets.connect(uri) as ws:
                for msg in messages:
                    await ws.send(json.dumps(msg))
                    replies.append(json.loads(await ws.recv()))
            return replies

        try:
            return asyncio.run(_run())
        except Exception as e:  # noqa: BLE001
            raise FreecivClientError("WS submit to {} failed: {}".format(uri, e))


# --------------------------------------------------------------------------- self-test

# Upstream freeciv-proxy required top-level fields, mirrored here so the contract is
# asserted offline (source: freeciv-proxy/message_validator.py:75 and :133).
_LLM_CONNECT_REQUIRED = ("type", "agent_id", "api_token")
_ACTION_REQUIRED = ("type", "action")


def _selftest():
    """Offline self-test: no network. Drives submit_action() through a fake websockets
    module and asserts the exact wire sequence against the upstream proxy contract."""
    import asyncio
    import json as _json
    import sys as _sys

    c = FreecivClient(
        proxy_url="http://h:8002", ws_url="ws://h:8002/llmsocket/8002",
        api_token="test-token-fc3d-001", game_id="test_game", player_id=1,
        agent_id="omegaclaw", civserver_port=6001)

    # 1. llm_connect carries top-level api_token (the E220 regression guard).
    cm = c.connect_message()
    for f in _LLM_CONNECT_REQUIRED:
        assert f in cm, "llm_connect missing top-level {!r}: {}".format(f, cm)
    assert cm["api_token"] == "test-token-fc3d-001"
    assert "data" not in cm, "api_token must NOT be nested under data"

    # 2. action / end_turn envelopes satisfy the action schema.
    am = action_message({"type": "unit_move", "unit_id": 7, "dest_x": 5, "dest_y": 5})
    for f in _ACTION_REQUIRED:
        assert f in am, am
    assert am["action"]["actor_id"] == 7 and am["action"]["action_type"] == "unit_move", am
    assert end_turn_message() == {"type": "action", "action": {"action_type": "end_turn"}}
    # actor_id preserved when passed directly, without a unit_id.
    assert action_message({"type": "unit_fortify", "actor_id": 9})["action"]["actor_id"] == 9

    # 3. URL is used verbatim — never suffixed with the gateway /ws/agent route.
    assert c._ws_uri() == "ws://h:8002/llmsocket/8002"
    assert "/ws/agent/" not in c._ws_uri()

    # 4. Drive submit_action() end-to-end through a fake websockets module; capture frames.
    sent = []

    class _FakeWS:
        async def send(self, data):
            sent.append(_json.loads(data))

        async def recv(self):
            # echo an ack keyed to the last sent type
            last = sent[-1]["type"]
            return _json.dumps({"type": "auth_success" if last == "llm_connect" else "action_ack"})

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

    saved = _sys.modules.get("websockets")
    _sys.modules["websockets"] = _FakeWebsockets()
    try:
        replies = c.submit_action({"type": "end_turn"})
    finally:
        if saved is not None:
            _sys.modules["websockets"] = saved
        else:
            _sys.modules.pop("websockets", None)

    assert _FakeConnect.uri_seen == "ws://h:8002/llmsocket/8002", _FakeConnect.uri_seen
    assert len(sent) == 2, sent
    assert sent[0]["type"] == "llm_connect" and sent[0]["api_token"] == "test-token-fc3d-001"
    assert "data" not in sent[0]
    assert sent[1] == {"type": "action", "action": {"action_type": "end_turn"}}, sent[1]
    assert len(replies) == 2 and replies[0]["type"] == "auth_success"

    print("freeciv client self-tests passed")


if __name__ == "__main__":
    _selftest()
