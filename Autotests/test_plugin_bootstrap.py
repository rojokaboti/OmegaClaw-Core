"""Plugin bootstrap smoke test (upstream plugin-runtime migration).

Loads ``config/plugins.yaml`` and exercises every channel/provider plugin's registration and its
``config()`` argument-mapping WITHOUT any external network calls. This is the check that would
have caught the ``channels/mattermost.py`` ``start_mattermost(url, channel_id)`` typo (an undefined
variable in a ``config()`` wrapper only surfaces when that channel is selected at runtime).

Pure-Python, host-runnable. Runs under pytest and standalone
(``python3 Autotests/test_plugin_bootstrap.py``). Heavy/optional deps (openai, websockets) are
stubbed and each channel's ``start_*`` entrypoint is neutralized, so nothing connects.
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

# Stub heavy/optional network deps so import + config() never touch the network. These are the
# runtime libraries the channel/provider modules import at module load (openai, websockets,
# websocket-client, requests); the container ships them, the host CI sweep does not need them.
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

_PLUGINS_YAML = os.path.join(_REPO_ROOT, "config", "plugins.yaml")

# Channel plugin -> its module-level start entrypoint (neutralized so config() maps args w/o connecting).
_CHANNEL_START_FNS = {
    "irc": "start_irc", "telegram": "start_telegram", "slack": "start_slack",
    "mattermost": "start_mattermost", "wschat": "start_websocket", "mockchannel": "start_mock",
}
_EXPECTED_PLUGINS = {"irc", "telegram", "slack", "mattermost", "mockchannel", "wschat",
                     "openaiapi", "asione", "openrouter", "openai", "mockprovider"}
_EXPECTED_CHANNEL_IDS = {"irc", "telegram", "slack", "mattermost", "test", "websocket"}
_EXPECTED_PROVIDER_IDS = {"OpenAIAPI", "ASICloud", "Anthropic", "SNET", "Test"}


def _load_plugin_records():
    with open(_PLUGINS_YAML, encoding="utf-8") as f:
        records = yaml.safe_load(f)
    assert isinstance(records, list) and records, "config/plugins.yaml must be a non-empty list"
    for r in records:
        assert "name" in r and "loader" in r, f"plugin record missing name/loader: {r}"
    return records


def _register_all():
    for r in _load_plugin_records():
        if r.get("loader") != "python":
            continue
        mod = importlib.import_module(r["name"])
        assert hasattr(mod, "loadOmegaClawPlugin"), f"{r['name']} has no loadOmegaClawPlugin()"
        mod.loadOmegaClawPlugin()


def test_plugins_yaml_parses_and_lists_expected():
    names = {r["name"] for r in _load_plugin_records()}
    missing = _EXPECTED_PLUGINS - names
    assert not missing, f"config/plugins.yaml missing expected plugins: {missing}"


def test_every_plugin_registers():
    _register_all()
    assert _EXPECTED_CHANNEL_IDS <= set(pluginapi._commChannelRegistry), \
        f"channels registered: {sorted(pluginapi._commChannelRegistry)}"
    assert _EXPECTED_PROVIDER_IDS <= set(pluginapi._llmProviderRegistry), \
        f"providers registered: {sorted(pluginapi._llmProviderRegistry)}"


def test_channel_config_wrappers_map_args_without_network():
    """Neutralize each channel's start_* entrypoint, then call config() to exercise arg mapping."""
    _register_all()
    for name, start_fn in _CHANNEL_START_FNS.items():
        mod = importlib.import_module(name)
        if hasattr(mod, start_fn):
            setattr(mod, start_fn, lambda *a, **k: None)
    for cid, channel in pluginapi._commChannelRegistry.items():
        channel.config({})  # must not raise (undefined-variable typos surface here)


def test_provider_config_wrappers_build_without_network():
    _register_all()
    for pid, provider in pluginapi._llmProviderRegistry.items():
        provider.config({})  # constructor only; client is lazy and openai is stubbed


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"All {len(fns)} plugin_bootstrap tests passed")


if __name__ == "__main__":
    _run()
