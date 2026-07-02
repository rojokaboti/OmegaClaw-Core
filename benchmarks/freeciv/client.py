"""Thin client for the ``taso-ventures/freeciv-llm`` service.

Two surfaces (see freeciv-proxy/API_DOCUMENTATION.md):
  - HTTP proxy (``:8002``): ``get_state`` / ``get_legal_actions`` (stdlib urllib — no deps).
  - WS gateway (``:8003``): ``submit_action`` via ``llm_connect`` -> ``action_submit``.
    WebSocket support is imported lazily (``websockets``) so importing this module — and the
    whole deterministic adapter — needs no extra packages on the host. The WS dependency is
    only touched on the live submission path (Docker phase).

All endpoints/credentials come from the environment so nothing is hardcoded into OmegaClaw:
  FREECIV_PROXY_URL, FREECIV_WS_URL, FREECIV_API_TOKEN, FREECIV_GAME_ID, FREECIV_PLAYER_ID,
  FREECIV_AGENT_ID, FREECIV_CIVSERVER_PORT.
"""

import json
import os
import urllib.request


class FreecivClientError(Exception):
    pass


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
        return cls(
            proxy_url=os.environ.get("FREECIV_PROXY_URL", "http://host.docker.internal:8002"),
            ws_url=os.environ.get("FREECIV_WS_URL", "ws://host.docker.internal:8003"),
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

    # ---- WS (gateway :8003) ----------------------------------------------------------
    def submit_action(self, action):
        """Submit a (normalized) action: llm_connect then action_submit. Returns the reply."""
        action_type = action.get("type") or action.get("action_type")
        data = {k: v for k, v in action.items() if k not in ("type", "action_type")}
        data["action_type"] = action_type
        messages = [
            {"type": "llm_connect", "agent_id": self.agent_id,
             "data": {"api_token": self.api_token, "port": self.civserver_port}},
            {"type": "action_submit", "data": data},
        ]
        return self._ws_roundtrip(messages)

    def _ws_roundtrip(self, messages):
        try:
            import asyncio
            import websockets  # live-only dependency
        except ImportError as e:
            raise FreecivClientError(
                "websocket submit requires the 'websockets' package (live/in-container only): {}".format(e))

        uri = "{}/ws/agent/{}?game_id={}".format(self.ws_url, self.agent_id, self.game_id)

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
