"""Declarative LLM provider/model configuration (Issue #4).

Provider names, API-key env vars, models, base URLs, api styles and (optional)
reasoning settings live in ``profile/llm_providers.yaml`` instead of being
hardcoded in ``lib_llm_ext.py``. Switching provider/model is then a YAML/env
change, no Python edit.

Selected via ``OMEGACLAW_LLM_CONFIG_PATH`` (relative paths resolve against the
install root, not the process CWD). **Fail-open:** a missing/invalid config logs a
warning and falls back to ``_BUILTIN_DEFAULTS`` (which mirror the shipped YAML), so
the agent never bricks on a misconfiguration.
"""

from __future__ import annotations

import copy
import os

import yaml

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_POLICY_PATH = os.path.join(_REPO_ROOT, "profile", "llm_providers.yaml")

_VALID_API_STYLES = {"chat_completions", "responses", "asione"}

# Built-in defaults: the fail-open net AND the canonical values mirrored by the
# shipped profile/llm_providers.yaml. The Test provider is registered in code
# (lib_llm_ext), not here, because it is environment-driven, not config-driven.
_BUILTIN_DEFAULTS = {
    "version": 1,
    "default_provider": "Anthropic",
    "providers": {
        "ASICloud": {
            "api_key_env": "ASI_API_KEY", "model": "minimax/minimax-m2.5",
            "base_url": "https://inference.asicloud.cudos.org/v1", "api_style": "chat_completions",
        },
        "Anthropic": {
            "api_key_env": "ANTHROPIC_API_KEY", "model": "claude-opus-4-6",
            "base_url": "https://api.anthropic.com/v1/", "api_style": "chat_completions",
        },
        "Ollama-local": {
            "api_key_env": "OLLAMA_API_KEY", "model": "qwen3.5:9b",
            "base_url": "http://localhost:11434/v1", "api_style": "chat_completions",
        },
        "ASIOne": {
            "api_key_env": "ASIONE_API_KEY", "model": "asi1-ultra",
            "base_url": "https://api.asi1.ai/v1", "api_style": "asione",
        },
        "OpenRouter": {
            "api_key_env": "OPENROUTER_API_KEY", "model": "z-ai/glm-5.1",
            "base_url": "https://openrouter.ai/api/v1", "api_style": "chat_completions",
            "reasoning": {"enabled": True, "max_tokens": 6000, "exclude": True},
        },
        "OpenAI": {
            "api_key_env": "OPENAI_API_KEY", "model": "gpt-5.4",
            "base_url": "https://api.openai.com/v1", "api_style": "responses",
        },
    },
}

_CACHE = {}
_WARNED = set()


def builtin_defaults():
    """A deep copy of the built-in default config."""
    return copy.deepcopy(_BUILTIN_DEFAULTS)


def config_path():
    """Active config path. A relative OMEGACLAW_LLM_CONFIG_PATH resolves against
    the install root (not the process CWD), so it works wherever the agent runs."""
    env = os.environ.get("OMEGACLAW_LLM_CONFIG_PATH")
    if not env:
        return _DEFAULT_POLICY_PATH
    return env if os.path.isabs(env) else os.path.join(_REPO_ROOT, env)


def validate_config(cfg):
    """Return an error string if ``cfg`` is structurally invalid, else None."""
    if not isinstance(cfg, dict):
        return "config root is not a mapping"
    providers = cfg.get("providers")
    if not isinstance(providers, dict) or not providers:
        return "config has no 'providers' mapping"
    default = cfg.get("default_provider")
    if default not in providers:
        return f"default_provider {default!r} not in providers"
    for name, entry in providers.items():
        if not isinstance(entry, dict):
            return f"provider {name!r} is not a mapping"
        for required in ("api_key_env", "model", "base_url"):
            if not entry.get(required):
                return f"provider {name!r} missing required field {required!r}"
        style = entry.get("api_style", "chat_completions")
        if style not in _VALID_API_STYLES:
            return f"provider {name!r} has unknown api_style {style!r} (allowed: {sorted(_VALID_API_STYLES)})"
    return None


def _fallback(message):
    path = config_path()
    if path not in _WARNED:
        print(f"[provider_config] WARNING {message}; using built-in defaults", flush=True)
        _WARNED.add(path)
    return builtin_defaults()


def load_config(path=None):
    """Load + validate the provider config, caching by (path, mtime).

    Fail-open: missing, unparseable, or invalid config returns the built-in
    defaults (with a one-time warning) so provider selection never bricks the agent.
    """
    path = path or config_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return _fallback(f"provider config not found ({path})")

    key = (path, mtime)
    if key in _CACHE:
        return _CACHE[key]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        result = _fallback(f"failed to parse provider config ({path}): {exc}")
        _CACHE[key] = result
        return result

    err = validate_config(data)
    if err:
        result = _fallback(f"invalid provider config ({path}): {err}")
        _CACHE[key] = result
        return result

    _CACHE[key] = data
    return data


def reset_cache():
    """Test helper."""
    _CACHE.clear()
    _WARNED.clear()


def default_provider(cfg=None):
    return (cfg or load_config()).get("default_provider")


def provider_entry(name, cfg=None):
    return ((cfg or load_config()).get("providers") or {}).get(name)


def config_model(name, cfg=None):
    """The model configured for ``name`` (per config), or '' if unknown."""
    entry = provider_entry(name, cfg)
    return entry.get("model", "") if entry else ""


if __name__ == "__main__":
    # Self-tests (no Docker / no openai needed).
    import tempfile

    base = load_config(_DEFAULT_POLICY_PATH) if os.path.exists(_DEFAULT_POLICY_PATH) else builtin_defaults()
    assert validate_config(builtin_defaults()) is None
    assert default_provider(builtin_defaults()) == "Anthropic"
    assert config_model("OpenAI", builtin_defaults()) == "gpt-5.4"
    assert config_model("Anthropic", builtin_defaults()) == "claude-opus-4-6"

    # invalid configs caught
    assert validate_config({"providers": {}}) is not None
    assert validate_config({"default_provider": "X", "providers": {"Y": {"api_key_env": "K", "model": "m", "base_url": "u"}}}) is not None
    assert validate_config({"default_provider": "Y", "providers": {"Y": {"model": "m", "base_url": "u"}}}) is not None  # missing env
    assert validate_config({"default_provider": "Y", "providers": {"Y": {"api_key_env": "K", "model": "m", "base_url": "u", "api_style": "bogus"}}}) is not None

    # missing path -> fallback to builtin
    reset_cache()
    os.environ["OMEGACLAW_LLM_CONFIG_PATH"] = "/nonexistent/llm.yaml"
    try:
        cfg = load_config()
        assert cfg["default_provider"] == "Anthropic"
    finally:
        os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
        reset_cache()

    # relative path resolves against repo root regardless of CWD
    cwd = os.getcwd()
    os.chdir(tempfile.gettempdir())
    os.environ["OMEGACLAW_LLM_CONFIG_PATH"] = "profile/llm_providers.yaml"
    reset_cache()
    try:
        if os.path.exists(_DEFAULT_POLICY_PATH):
            cfg = load_config()
            assert "providers" in cfg and config_model("OpenAI", cfg) == "gpt-5.4"
    finally:
        os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
        reset_cache()
        os.chdir(cwd)

    print("provider_config self-tests passed")
