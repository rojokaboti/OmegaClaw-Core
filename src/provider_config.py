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

# Returned by load_config() when an EXPLICITLY configured config cannot be loaded
# and fail-open was not opted into: callers must NOT fall back to built-in (cloud)
# providers (privacy: an explicit private/local config that fails must never
# silently route prompts to an external default).
FAIL_CLOSED = object()

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


def _is_explicit(path_arg):
    """True when the config was explicitly requested (path arg or env var)."""
    return path_arg is not None or bool(os.environ.get("OMEGACLAW_LLM_CONFIG_PATH"))


def _fail_open_opt_in():
    return (os.environ.get("OMEGACLAW_LLM_CONFIG_FAIL_OPEN") or "").strip().lower() in {"1", "true", "yes", "on"}


def _on_unloadable(path, message, explicit):
    """Decide what an unloadable config returns.

    * not explicit (no env; shipped default absent)          -> built-in defaults (fail-open).
    * explicit + OMEGACLAW_LLM_CONFIG_FAIL_OPEN opt-in        -> built-in defaults (fail-open).
    * explicit, no opt-in                                     -> FAIL_CLOSED (no external routing).
    """
    if explicit and not _fail_open_opt_in():
        if path not in _WARNED:
            print(
                f"[provider_config] SECURITY explicit OMEGACLAW_LLM_CONFIG_PATH could not be loaded "
                f"({path}): {message}; refusing to fall back to built-in providers "
                f"(set OMEGACLAW_LLM_CONFIG_FAIL_OPEN=1 to override)",
                flush=True,
            )
            _WARNED.add(path)
        return FAIL_CLOSED
    if path not in _WARNED:
        print(f"[provider_config] WARNING {message} ({path}); using built-in defaults", flush=True)
        _WARNED.add(path)
    return builtin_defaults()


def load_config(path=None):
    """Load + validate the provider config, caching by (path, mtime).

    Failure model (privacy-aware): an **absent shipped default** (no env set) fails open to
    the built-in defaults so the agent never bricks out-of-box. But an **explicitly supplied**
    ``OMEGACLAW_LLM_CONFIG_PATH`` that is missing/unparseable/invalid fails **closed**
    (returns :data:`FAIL_CLOSED`) so prompts can never be silently routed to a built-in cloud
    provider; set ``OMEGACLAW_LLM_CONFIG_FAIL_OPEN=1`` to opt back into fallback.
    """
    explicit = _is_explicit(path)
    path = path or config_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return _on_unloadable(path, "provider config not found", explicit)

    key = (path, mtime)
    if key in _CACHE:
        return _CACHE[key]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        result = _on_unloadable(path, f"failed to parse: {exc}", explicit)
        _CACHE[key] = result
        return result

    err = validate_config(data)
    if err:
        result = _on_unloadable(path, f"invalid config: {err}", explicit)
        _CACHE[key] = result
        return result

    _CACHE[key] = data
    return data


def reset_cache():
    """Test helper."""
    _CACHE.clear()
    _WARNED.clear()


def default_provider(cfg=None):
    cfg = cfg if cfg is not None else load_config()
    if cfg is FAIL_CLOSED:
        return None
    return cfg.get("default_provider")


def provider_entry(name, cfg=None):
    cfg = cfg if cfg is not None else load_config()
    if cfg is FAIL_CLOSED:
        return None
    return (cfg.get("providers") or {}).get(name)


def config_model(name, cfg=None):
    """The model configured for ``name`` (per config), or '' if unknown/fail-closed."""
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

    # explicit missing path -> FAIL_CLOSED (no silent fallback to cloud defaults)
    reset_cache()
    os.environ["OMEGACLAW_LLM_CONFIG_PATH"] = "/nonexistent/llm.yaml"
    try:
        assert load_config() is FAIL_CLOSED
        assert default_provider() is None and config_model("Anthropic") == ""
    finally:
        os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
        reset_cache()

    # explicit missing + opt-in -> falls back to builtin
    reset_cache()
    os.environ["OMEGACLAW_LLM_CONFIG_PATH"] = "/nonexistent/llm.yaml"
    os.environ["OMEGACLAW_LLM_CONFIG_FAIL_OPEN"] = "1"
    try:
        cfg = load_config()
        assert cfg is not FAIL_CLOSED and cfg["default_provider"] == "Anthropic"
    finally:
        os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
        os.environ.pop("OMEGACLAW_LLM_CONFIG_FAIL_OPEN", None)
        reset_cache()

    # no env, explicit path arg missing -> still explicit -> FAIL_CLOSED
    reset_cache()
    assert load_config("/nonexistent/explicit.yaml") is FAIL_CLOSED
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
