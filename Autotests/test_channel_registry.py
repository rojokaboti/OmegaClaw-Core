"""Unit tests for the communication-channel registry (Issue #9).

Pure-Python, no Docker/real-channel deps (the registry lazy-imports channel modules, so importing
it here pulls in nothing). Runs under pytest and standalone
(`python3 Autotests/test_channel_registry.py`). Covers dispatch, unknown->mock fallback, the
outgoing newline escape, and that adding a channel is one `register(...)` call.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import channel_registry as cr  # noqa: E402


def _fake(name, inbox=None):
    rec = {"start": None, "sent": [], "inbox": list(inbox or [])}
    ch = cr.Channel(
        name,
        start=lambda cfg: rec.__setitem__("start", cfg),
        receive=lambda: (rec["inbox"].pop(0) if rec["inbox"] else ""),
        send=lambda m: rec["sent"].append(m),
    )
    return ch, rec


def test_real_channels_registered():
    for name in ("irc", "telegram", "slack", "mattermost", "websocket", "mock"):
        assert name in cr.list_channels()
        assert isinstance(cr.CHANNELS[name], cr.Channel)


def test_start_receive_send_dispatch_to_selected_channel():
    ch, rec = _fake("echo", inbox=["hi there"])
    cr.register(ch)
    try:
        assert cr.start_channel("echo", irc_channel="x") == "CHANNEL-STARTED:echo"
        assert rec["start"]["IRC_channel"] == "x"          # config passed through
        assert cr.receive("echo") == "hi there"            # receive dispatches
        assert cr.receive("echo") == ""                    # empty when drained
        cr.send("echo", "hello")
        assert rec["sent"] == ["hello"]
    finally:
        cr.CHANNELS.pop("echo", None)


def test_send_escapes_newlines():
    ch, rec = _fake("echo")
    cr.register(ch)
    try:
        cr.send("echo", "line1\nline2\nline3")
        assert rec["sent"] == ["line1\\nline2\\nline3"]    # "\n" -> "\\n" like the old send
        cr.send("echo", None)                              # None is tolerated
        assert rec["sent"][-1] == ""
    finally:
        cr.CHANNELS.pop("echo", None)


def test_unknown_channel_falls_back_to_mock():
    # _resolve maps an unknown name to the mock entry (old else-fallthrough), no import triggered.
    assert cr._resolve("does-not-exist").name == "mock"
    assert cr._resolve("test").name == "mock"
    # and dispatch actually routes to the fallback: override mock with a fake to observe it
    ch, rec = _fake("mock", inbox=["from-mock"])
    real_mock = cr.CHANNELS["mock"]
    cr.register(ch)
    try:
        assert cr.receive("weird-unknown") == "from-mock"
        cr.send("weird-unknown", "x\ny")
        assert rec["sent"] == ["x\\ny"]
    finally:
        cr.CHANNELS["mock"] = real_mock


def test_adding_a_channel_is_one_object():
    before = set(cr.list_channels())
    ch, rec = _fake("echo2", inbox=["yo"])
    cr.register(ch)                                          # <-- the whole cost of a new channel
    try:
        assert set(cr.list_channels()) - before == {"echo2"}
        assert cr.receive("echo2") == "yo"                  # fully dispatchable immediately
        cr.start_channel("echo2")
        cr.send("echo2", "hi")
        assert rec["start"] is not None and rec["sent"] == ["hi"]
    finally:
        cr.CHANNELS.pop("echo2", None)


def test_start_channel_maps_config_per_channel():
    # config keys are routed to the right channel's start builder (fake overrides real ones)
    seen = {}
    for name, key, val in (("irc", "irc_server", "myserver"), ("telegram", "tg_chat_id", "42"),
                           ("mattermost", "mm_url", "http://mm")):
        ch, rec = _fake(name)
        saved = cr.CHANNELS[name]
        cr.register(ch)
        try:
            cr.start_channel(name, **{key: val})
            seen[name] = rec["start"]
        finally:
            cr.CHANNELS[name] = saved
    assert seen["irc"]["IRC_server"] == "myserver"
    assert seen["telegram"]["TG_CHAT_ID"] == "42"
    assert seen["mattermost"]["MM_URL"] == "http://mm"


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
    print("\nAll {} channel_registry tests passed".format(len(fns)))
