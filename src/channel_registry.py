"""Communication-channel registry (Issue #9).

Replaces the three nested-`if` dispatchers in ``src/channels.metta`` (start / receive / send) with
one Python table: each channel is a :class:`Channel` with ``start``/``receive``/``send``. The MeTTa
facade becomes thin ``py-call``s into ``start_channel`` / ``receive`` / ``send`` here. Adding a
channel is registering one object instead of editing three branches.

Design constraints honored:
- **Real module functions, module identity preserved.** Each channel keeps module-level global
  state (threads, sockets), so the registry calls the real ``channels/<name>.py`` functions — it
  never copies or re-instantiates them.
- **Lazy, import-light.** Channel modules are imported only when a channel is actually used (via
  ``_lazy``), so importing this module pulls in no channel deps and it is host-unit-testable. In
  particular ``channels/mock.py`` (test-only) is loaded only when the mock channel is selected.
- **Unknown channel -> mock**, matching the old else-fallthrough (``-t test`` etc.).
- The ``&lastsend`` duplicate-send shield and ``(cut)`` stay in the MeTTa facade; the registry only
  dispatches and applies the outgoing newline escape (``"\n" -> "\\n"``) that ``send`` did before.
"""

import importlib
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict
from urllib.parse import urlparse

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

FALLBACK = "mock"


@dataclass
class Channel:
    name: str
    start: Callable[[dict], Any]      # (config) -> Any  (spawns the channel)
    receive: Callable[[], Any]        # () -> message str
    send: Callable[[str], Any]        # (message) -> Any
    default_config: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- lazy loading

def _import_channel(mod):
    """Import a channel module lazily, trying the bare name then the ``channels`` package."""
    last = None
    for cand in (mod, "channels." + mod):
        try:
            return importlib.import_module(cand)
        except ImportError as exc:
            last = exc
    raise last


def _lazy(mod, fn):
    """A callable that resolves ``<mod>.<fn>`` at call time (no import at registry import)."""
    def _call(*args, **kwargs):
        return getattr(_import_channel(mod), fn)(*args, **kwargs)
    return _call


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- start builders
# Each maps the resolved config dict to the module's heterogeneous start_* positional args,
# applying the historical defaults.

def _irc_start(cfg):
    return _lazy("irc", "start_irc")(
        cfg.get("IRC_channel") or "##omegaclaw",
        cfg.get("IRC_server") or "irc.quakenet.org",
        _int(cfg.get("IRC_port"), 6667),
        cfg.get("IRC_user") or "omegaclaw",
    )


def _telegram_start(cfg):
    return _lazy("telegram", "start_telegram")(cfg.get("TG_CHAT_ID") or "",
                                               _int(cfg.get("TG_POLL_TIMEOUT"), 20))


def _slack_start(cfg):
    return _lazy("slack", "start_slack")(cfg.get("SL_CHANNEL_ID") or "",
                                         _int(cfg.get("SL_POLL_INTERVAL"), 60))


def _mattermost_start(cfg):
    return _lazy("mattermost", "start_mattermost")(
        cfg.get("MM_URL") or "https://chat.singularitynet.io",
        cfg.get("MM_CHANNEL_ID") or "8fjrmabjx7gupy7e5kjznpt5qh",
    )


def _ws_insecure_opt_in():
    return str(os.environ.get("OMEGACLAW_WS_ALLOW_INSECURE", "")).strip().lower() in (
        "1", "true", "yes", "on")


def _ws_scheme_ok(ws_url):
    """Return (ok, reason). The websocket channel is an agent control plane carrying the bearer
    token + inbound command frames, so cleartext ``ws://`` is rejected by default (interception /
    tampering risk). ``ws://`` is allowed only for loopback hosts (traffic never leaves the box)
    or when ``OMEGACLAW_WS_ALLOW_INSECURE=1`` is set as an explicit unsafe/dev opt-in."""
    parsed = urlparse(ws_url)
    scheme = (parsed.scheme or "").lower()
    if scheme == "wss":
        return True, ""
    if scheme != "ws":
        return False, "WS_URL must use wss:// (or ws:// for loopback); got scheme {!r}".format(scheme)
    host = (parsed.hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return True, ""
    if _ws_insecure_opt_in():
        print("[channel_registry] WARNING: starting websocket over CLEARTEXT ws:// to {!r} "
              "(OMEGACLAW_WS_ALLOW_INSECURE set) — bearer token and control frames are "
              "unencrypted on the network.".format(host or ws_url))
        return True, ""
    return False, ("cleartext ws:// to non-loopback host {!r} is refused; use wss:// or set "
                   "OMEGACLAW_WS_ALLOW_INSECURE=1 for an explicit unsafe/dev opt-in".format(host or ws_url))


def _websocket_start(cfg):
    """Start the websocket channel, fail-closed.

    The websocket channel is effectively an agent control plane: inbound frames drive the agent.
    Unlike the polling adapters it has no in-frame ``auth <secret>`` ownership gate, so we refuse
    to bring it up against an unauthenticated or cleartext endpoint. Both ``WS_URL`` and a
    non-empty ``WS_TOKEN`` (sent as ``Authorization: Bearer``) are required, and ``WS_URL`` must
    be ``wss://`` (cleartext ``ws://`` allowed only for loopback or with an explicit opt-in — see
    ``_ws_scheme_ok``). On any violation we DECLINE to start (returning ``False`` so
    ``start_channel`` reports it truthfully as disabled) rather than connecting to / advertising
    an open or interceptable control path.
    """
    ws_url = (cfg.get("WS_URL") or "").strip()
    ws_token = (cfg.get("WS_TOKEN") or "").strip()
    missing = [k for k, v in (("WS_URL", ws_url), ("WS_TOKEN", ws_token)) if not v]
    if missing:
        print("[channel_registry] websocket channel disabled: {} required (fail-closed)".format(
            " and ".join(missing)))
        return False
    scheme_ok, reason = _ws_scheme_ok(ws_url)
    if not scheme_ok:
        print("[channel_registry] websocket channel disabled: {}".format(reason))
        return False
    return _lazy("wschat", "start_websocket")(ws_url, ws_token) or False


def _mock_start(cfg):
    return _lazy("mock", "start_mock")()


CHANNELS: Dict[str, Channel] = {
    "irc": Channel("irc", _irc_start, _lazy("irc", "getLastMessage"), _lazy("irc", "send_message"),
                   {"IRC_channel": "##omegaclaw", "IRC_server": "irc.quakenet.org",
                    "IRC_port": 6667, "IRC_user": "omegaclaw"}),
    "telegram": Channel("telegram", _telegram_start, _lazy("telegram", "getLastMessage"),
                        _lazy("telegram", "send_message"), {"TG_CHAT_ID": "", "TG_POLL_TIMEOUT": 20}),
    "slack": Channel("slack", _slack_start, _lazy("slack", "getLastMessage"),
                     _lazy("slack", "send_message"), {"SL_CHANNEL_ID": "", "SL_POLL_INTERVAL": 60}),
    "mattermost": Channel("mattermost", _mattermost_start, _lazy("mattermost", "getLastMessage"),
                          _lazy("mattermost", "send_message"),
                          {"MM_URL": "https://chat.singularitynet.io",
                           "MM_CHANNEL_ID": "8fjrmabjx7gupy7e5kjznpt5qh"}),
    "websocket": Channel("websocket", _websocket_start, _lazy("wschat", "getLastMessage"),
                         _lazy("wschat", "send_message"), {"WS_URL": "", "WS_TOKEN": ""}),
    "mock": Channel("mock", _mock_start, _lazy("mock", "getLastMessage"),
                    _lazy("mock", "send_message"), {}),
}


# --------------------------------------------------------------------------- registry API

def register(channel):
    """Add or override a channel. Adding a new channel is this single call."""
    CHANNELS[channel.name] = channel
    return channel


def list_channels():
    return sorted(CHANNELS)


def _resolve(name):
    """Resolve a channel by name, falling back to mock for unknown names (old else-branch)."""
    return CHANNELS.get(str(name), CHANNELS[FALLBACK])


def start_channel(name, irc_channel="", irc_server="", irc_port="", irc_user="",
                  tg_chat_id="", tg_poll_timeout="", sl_channel_id="", sl_poll_interval="",
                  mm_url="", mm_channel_id="", ws_url="", ws_token=""):
    """Start the selected channel. Config is passed positionally from the MeTTa facade (already
    resolved from CLI args); only the selected channel's keys are used."""
    cfg = {
        "IRC_channel": irc_channel, "IRC_server": irc_server, "IRC_port": irc_port, "IRC_user": irc_user,
        "TG_CHAT_ID": tg_chat_id, "TG_POLL_TIMEOUT": tg_poll_timeout,
        "SL_CHANNEL_ID": sl_channel_id, "SL_POLL_INTERVAL": sl_poll_interval,
        "MM_URL": mm_url, "MM_CHANNEL_ID": mm_channel_id,
        "WS_URL": ws_url, "WS_TOKEN": ws_token,
    }
    ch = _resolve(name)
    # A channel may decline to start (e.g. websocket without WS_URL/WS_TOKEN); reflect that
    # truthfully instead of reporting a channel that isn't actually running. Only an explicit
    # False means "declined" — channels that legitimately return None (e.g. mock) are started.
    if ch.start(cfg) is False:
        return "CHANNEL-DISABLED:" + ch.name
    return "CHANNEL-STARTED:" + ch.name


def receive(name):
    """Return the latest message from the selected channel (empty string if none)."""
    msg = _resolve(name).receive()
    return "" if msg is None else msg


def send(name, message):
    """Escape outgoing newlines (as the old send did) then dispatch to the channel."""
    safe = "" if message is None else str(message).replace("\n", "\\n")
    return _resolve(name).send(safe)


# --------------------------------------------------------------------------- self-test

def _selftest():
    """Lightweight self-tests runnable without pytest/Docker (uses a fake channel, no real imports)."""
    calls = {"start": None, "sent": [], "inbox": ["hi there"]}

    fake = Channel(
        "echo",
        start=lambda cfg: calls.__setitem__("start", cfg),
        receive=lambda: (calls["inbox"].pop(0) if calls["inbox"] else ""),
        send=lambda m: calls["sent"].append(m),
    )
    register(fake)
    try:
        # start passes config through to the selected channel
        assert start_channel("echo", irc_channel="x") == "CHANNEL-STARTED:echo"
        assert calls["start"]["IRC_channel"] == "x"
        # receive dispatches to the selected channel
        assert receive("echo") == "hi there" and receive("echo") == ""
        # send applies the newline escape then dispatches
        send("echo", "a\nb")
        assert calls["sent"] == ["a\\nb"], calls["sent"]
        # unknown channel resolves to the mock fallback (no import triggered by _resolve)
        assert _resolve("does-not-exist").name == "mock"
        # the real channels are registered (incl. the upstream websocket/wschat channel)
        for name in ("irc", "telegram", "slack", "mattermost", "websocket", "mock"):
            assert name in list_channels()
        # websocket is fail-closed: without WS_URL + WS_TOKEN, or over cleartext ws:// to a
        # non-loopback host, it declines to start (and does NOT import wschat), so start_channel
        # reports it as disabled rather than started.
        assert start_channel("websocket") == "CHANNEL-DISABLED:websocket"
        assert start_channel("websocket", ws_url="wss://x") == "CHANNEL-DISABLED:websocket"
        assert start_channel("websocket", ws_token="t") == "CHANNEL-DISABLED:websocket"
        assert start_channel("websocket", ws_url="ws://example.test", ws_token="t") == "CHANNEL-DISABLED:websocket"
        assert start_channel("websocket", ws_url="http://example.test", ws_token="t") == "CHANNEL-DISABLED:websocket"
    finally:
        CHANNELS.pop("echo", None)
    print("channel_registry self-tests passed")


if __name__ == "__main__":
    _selftest()
