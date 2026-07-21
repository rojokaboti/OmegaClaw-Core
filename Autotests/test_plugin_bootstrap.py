"""Plugin bootstrap smoke test (upstream plugin-runtime migration).

Runs the REAL plugin loader (``src/plugin.py::initPlugins``) against ``config/plugins.yaml`` and
then exercises every registered channel/provider plugin's ``config()`` argument-mapping WITHOUT any
external network calls. This is the check that would have caught the ``channels/mattermost.py``
``start_mattermost(url, channel_id)`` typo (an undefined variable in a ``config()`` wrapper only
surfaces when that channel is selected at runtime).

Using the real ``initPlugins`` matters: it loads each plugin by FILE PATH via
``spec_from_file_location`` (e.g. ``providers/openai.py``), so a plugin named ``openai`` is not
shadowed by the installed ``openai`` SDK — a naive ``import_module("openai")`` would be.

Pure-Python, host-runnable. Runs under pytest and standalone
(``python3 Autotests/test_plugin_bootstrap.py``). Heavy/optional network deps (openai, websockets,
websocket-client, requests) are stubbed and each channel's ``start_*`` entrypoint is neutralized,
so nothing connects.
"""
import importlib
import os
import sys
import types

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "src"),
           os.path.join(_REPO_ROOT, "providers"), os.path.join(_REPO_ROOT, "channels")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Stub heavy/optional network deps the channel/provider modules import at load. The container ships
# them; the host CI sweep does not need them. (If a real one is installed, e.g. openai in CI, it is
# used as-is — initPlugins loads plugin files by path, so there is no name collision either way.)
for _mod in ("openai", "websockets", "websocket", "requests"):
    if _mod not in sys.modules:
        try:
            importlib.import_module(_mod)
        except Exception:
            _m = types.ModuleType(_mod)
            if _mod == "openai":
                _m.OpenAI = object
            sys.modules[_mod] = _m

import yaml  # noqa: E402  (PyYAML is a real project dependency)
import pluginapi  # noqa: E402
import plugin  # noqa: E402  (src/plugin.py — the real runtime loader)

_PLUGINS_YAML = os.path.join(_REPO_ROOT, "config", "plugins.yaml")

# Channel plugin name -> its module-level start entrypoint (neutralized so config() maps args w/o connecting).
_CHANNEL_START_FNS = {
    "irc": "start_irc", "telegram": "start_telegram", "slack": "start_slack",
    "mattermost": "start_mattermost", "wschat": "start_websocket", "mockchannel": "start_mock",
}
_EXPECTED_PLUGINS = {"irc", "telegram", "slack", "mattermost", "mockchannel", "wschat",
                     "openaiapi", "asione", "openrouter", "openai", "mockprovider"}
_EXPECTED_CHANNEL_IDS = {"irc", "telegram", "slack", "mattermost", "test", "websocket"}
_EXPECTED_PROVIDER_IDS = {"OpenAIAPI", "ASICloud", "Anthropic", "SNET", "Test"}

_BOOTSTRAPPED = False


def _bootstrap_once():
    """Run the real initPlugins exactly once (it enforces name-uniqueness, so it is not re-entrant)."""
    global _BOOTSTRAPPED
    if not _BOOTSTRAPPED:
        plugin.initPlugins()
        _BOOTSTRAPPED = True


def _load_plugin_records():
    with open(_PLUGINS_YAML, encoding="utf-8") as f:
        records = yaml.safe_load(f)
    assert isinstance(records, list) and records, "config/plugins.yaml must be a non-empty list"
    for r in records:
        assert "name" in r and "loader" in r, f"plugin record missing name/loader: {r}"
    return records


def test_plugins_yaml_parses_and_lists_expected():
    names = {r["name"] for r in _load_plugin_records()}
    missing = _EXPECTED_PLUGINS - names
    assert not missing, f"config/plugins.yaml missing expected plugins: {missing}"


def test_initplugins_registers_channels_and_providers():
    _bootstrap_once()
    assert _EXPECTED_CHANNEL_IDS <= set(pluginapi._commChannelRegistry), \
        f"channels registered: {sorted(pluginapi._commChannelRegistry)}"
    assert _EXPECTED_PROVIDER_IDS <= set(pluginapi._llmProviderRegistry), \
        f"providers registered: {sorted(pluginapi._llmProviderRegistry)}"


def test_channel_config_wrappers_map_args_without_network():
    """Neutralize each channel's start_* entrypoint, then call config() to exercise arg mapping."""
    _bootstrap_once()
    for name, start_fn in _CHANNEL_START_FNS.items():
        mod = plugin._plugins[name].mod  # the file-loaded plugin module
        if hasattr(mod, start_fn):
            setattr(mod, start_fn, lambda *a, **k: None)
    for cid, channel in pluginapi._commChannelRegistry.items():
        channel.config({})  # must not raise (undefined-variable typos surface here)


def test_provider_config_wrappers_build_without_network():
    _bootstrap_once()
    for pid, provider in pluginapi._llmProviderRegistry.items():
        provider.config({})  # constructor only; client is lazy and openai is stubbed/unused


def test_asione_chat_safe_prompt_split():
    """Regression: ASIOne must use the safe prompt split (llm._split_system_user), not a bare
    content.split(':-:-:-:') that raises ValueError on a missing or extra delimiter — before any
    model call and outside the provider's own try/except. Exercises chat() with a stubbed client."""
    _bootstrap_once()
    provider = pluginapi._llmProviderRegistry["ASIOne"]
    provider.config({})
    captured = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["messages"] = kwargs.get("messages")
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))])

    # Inject a fake client so _ensure_client() is a no-op and no network call happens.
    provider.delegate._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=_FakeCompletions()))

    cases = {
        "no delimiter": ("just the user text", "", "just the user text"),
        "one delimiter": ("SYS:-:-:-:USER", "SYS", "USER"),
        "delimiter inside user text": ("SYS:-:-:-:USER with :-:-:-: inside",
                                       "SYS", "USER with :-:-:-: inside"),
    }
    for label, (content, exp_sys, exp_user) in cases.items():
        out = provider.chat(content)  # must NOT raise ValueError
        assert out == "ok", f"{label}: expected 'ok', got {out!r}"
        msgs = captured["messages"]
        assert msgs[0]["content"] == exp_sys, f"{label}: system={msgs[0]['content']!r}"
        assert msgs[1]["content"] == exp_user, f"{label}: user={msgs[1]['content']!r}"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"All {len(fns)} plugin_bootstrap tests passed")


if __name__ == "__main__":
    _run()
