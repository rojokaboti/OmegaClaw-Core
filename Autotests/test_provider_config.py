"""Unit tests for declarative provider/model config (Issue #4).

Pure-Python: no Docker. `lib_llm_ext` imports `openai` (absent on host/CI), so we
stub it before import. Runs under pytest and standalone
(`python3 Autotests/test_provider_config.py`).
"""
import os
import sys
import tempfile
import types

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Stub openai so importing lib_llm_ext works without the dependency.
if "openai" not in sys.modules:
    _stub = types.ModuleType("openai")
    _stub.OpenAI = object
    sys.modules["openai"] = _stub

import provider_config as pc  # noqa: E402
import lib_llm_ext as llm  # noqa: E402

_SHIPPED = os.path.join(_REPO_ROOT, "profile", "llm_providers.yaml")


def _write_tmp(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    f.write(text)
    f.close()
    return f.name


# --- config loading / validation -----------------------------------------

def test_builtin_defaults_valid():
    assert pc.validate_config(pc.builtin_defaults()) is None
    assert pc.default_provider(pc.builtin_defaults()) == "Anthropic"


def test_shipped_yaml_loads_and_matches_builtin():
    cfg = pc.load_config(_SHIPPED)
    assert pc.validate_config(cfg) is None
    # shipped YAML mirrors the built-in defaults (key fields)
    for name, entry in pc.builtin_defaults()["providers"].items():
        assert cfg["providers"][name]["model"] == entry["model"]
        assert cfg["providers"][name]["api_key_env"] == entry["api_key_env"]


def test_default_provider_resolution():
    tmp = _write_tmp("version: 1\ndefault_provider: OpenAI\n"
                     "providers:\n  OpenAI: {api_key_env: OPENAI_API_KEY, model: gpt-x, base_url: u, api_style: responses}\n")
    try:
        cfg = pc.load_config(tmp)
        assert pc.default_provider(cfg) == "OpenAI"
        assert pc.config_model("OpenAI", cfg) == "gpt-x"
    finally:
        os.unlink(tmp)


def test_missing_model_fails_validation():
    err = pc.validate_config({"default_provider": "A", "providers": {"A": {"api_key_env": "K", "base_url": "u"}}})
    assert err and "model" in err


def test_missing_api_key_env_fails_validation():
    err = pc.validate_config({"default_provider": "A", "providers": {"A": {"model": "m", "base_url": "u"}}})
    assert err and "api_key_env" in err


def test_unknown_api_style_fails_validation():
    err = pc.validate_config({"default_provider": "A",
                              "providers": {"A": {"api_key_env": "K", "model": "m", "base_url": "u", "api_style": "bogus"}}})
    assert err and "api_style" in err


def test_default_provider_must_exist():
    err = pc.validate_config({"default_provider": "Z",
                              "providers": {"A": {"api_key_env": "K", "model": "m", "base_url": "u"}}})
    assert err and "default_provider" in err


def test_no_env_does_not_fail_closed():
    # No explicit config -> shipped default (or built-in fallback), never FAIL_CLOSED.
    os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
    pc.reset_cache()
    try:
        cfg = pc.load_config()
        assert cfg is not pc.FAIL_CLOSED
        assert pc.config_model("OpenAI", cfg) == "gpt-5.4"
    finally:
        pc.reset_cache()


def test_explicit_missing_fails_closed():
    # SECURITY: an explicit-but-missing config must NOT fall back to cloud defaults.
    os.environ["OMEGACLAW_LLM_CONFIG_PATH"] = "/nonexistent/llm.yaml"
    pc.reset_cache()
    try:
        assert pc.load_config() is pc.FAIL_CLOSED
        assert pc.default_provider() is None
        assert pc.config_model("Anthropic") == ""
    finally:
        os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
        pc.reset_cache()


def test_explicit_invalid_fails_closed():
    tmp = _write_tmp("version: 1\ndefault_provider: X\nproviders: {}\n")
    os.environ["OMEGACLAW_LLM_CONFIG_PATH"] = tmp
    pc.reset_cache()
    try:
        assert pc.load_config() is pc.FAIL_CLOSED
    finally:
        os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
        pc.reset_cache()
        os.unlink(tmp)


def test_explicit_path_arg_missing_fails_closed():
    pc.reset_cache()
    try:
        assert pc.load_config("/nonexistent/explicit.yaml") is pc.FAIL_CLOSED
    finally:
        pc.reset_cache()


def test_fail_open_opt_in_restores_builtin():
    os.environ["OMEGACLAW_LLM_CONFIG_PATH"] = "/nonexistent/llm.yaml"
    os.environ["OMEGACLAW_LLM_CONFIG_FAIL_OPEN"] = "1"
    pc.reset_cache()
    try:
        cfg = pc.load_config()
        assert cfg is not pc.FAIL_CLOSED
        assert pc.config_model("OpenAI", cfg) == "gpt-5.4"  # builtin
    finally:
        os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
        os.environ.pop("OMEGACLAW_LLM_CONFIG_FAIL_OPEN", None)
        pc.reset_cache()


def test_relative_path_resolves_against_repo_root():
    cwd = os.getcwd()
    os.chdir(tempfile.gettempdir())
    os.environ["OMEGACLAW_LLM_CONFIG_PATH"] = "profile/llm_providers.yaml"
    pc.reset_cache()
    try:
        cfg = pc.load_config()
        assert pc.config_model("OpenAI", cfg) == "gpt-5.4"  # shipped file found, not CWD-relative
    finally:
        os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
        pc.reset_cache()
        os.chdir(cwd)


# --- lib_llm_ext registration + helpers ----------------------------------

def test_all_providers_registered_from_config():
    for name in ("ASICloud", "Anthropic", "Ollama-local", "ASIOne", "OpenRouter", "OpenAI", "Test"):
        assert llm._get_provider(name) is not None, name


def test_provider_classes_by_api_style():
    assert type(llm._get_provider("OpenAI")).__name__ == "OpenAIProvider"   # responses
    assert type(llm._get_provider("ASIOne")).__name__ == "AsiOneProvider"   # asione
    assert type(llm._get_provider("OpenRouter")).__name__ == "OpenRouterProvider"  # reasoning
    assert type(llm._get_provider("Anthropic")).__name__ == "AIProvider"    # chat_completions


def test_openrouter_reasoning_from_config():
    assert llm._get_provider("OpenRouter")._reasoning == {"enabled": True, "max_tokens": 6000, "exclude": True}


def test_effective_model_matches_config():
    assert llm.effective_model("Anthropic") == "claude-opus-4-6"
    assert llm.effective_model("OpenAI") == "gpt-5.4"
    assert llm.effective_model("Nonexistent") == ""


def test_describe_effective_config_shows_provider_and_model():
    desc = llm.describe_effective_config("Anthropic")
    assert "provider=Anthropic" in desc and "model=claude-opus-4-6" in desc


def test_describe_unknown_provider_is_clear():
    desc = llm.describe_effective_config("Bogus")
    assert "UNKNOWN" in desc


def test_fail_closed_registers_no_external_providers():
    # SECURITY end-to-end: an explicit-missing config must register NO cloud provider,
    # and callProvider for one must fail loudly (never silently route externally).
    saved = dict(llm._provider_registry)
    os.environ["OMEGACLAW_LLM_CONFIG_PATH"] = "/nonexistent/llm.yaml"
    pc.reset_cache()
    try:
        llm._provider_registry.clear()
        llm._register_from_config()
        assert llm._get_provider("Anthropic") is None
        assert llm._get_provider("OpenAI") is None
        assert llm._get_provider("Test") is not None  # mock harness still works
        raised = False
        try:
            llm.callProvider("Anthropic", "hi")
        except RuntimeError:
            raised = True
        assert raised, "callProvider should fail closed when no provider is registered"
    finally:
        os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
        pc.reset_cache()
        llm._provider_registry.clear()
        llm._provider_registry.update(saved)


def test_fail_open_opt_in_registers_providers():
    saved = dict(llm._provider_registry)
    os.environ["OMEGACLAW_LLM_CONFIG_PATH"] = "/nonexistent/llm.yaml"
    os.environ["OMEGACLAW_LLM_CONFIG_FAIL_OPEN"] = "1"
    pc.reset_cache()
    try:
        llm._provider_registry.clear()
        llm._register_from_config()
        assert llm._get_provider("Anthropic") is not None  # builtin restored
    finally:
        os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
        os.environ.pop("OMEGACLAW_LLM_CONFIG_FAIL_OPEN", None)
        pc.reset_cache()
        llm._provider_registry.clear()
        llm._provider_registry.update(saved)


# --- prompt split normalization ------------------------------------------

def test_split_system_user_with_delimiter():
    assert llm.split_system_user("SYS:-:-:-:USER") == ("SYS", "USER")


def test_split_system_user_without_delimiter():
    assert llm.split_system_user("only the user message") == ("", "only the user message")


def test_split_system_user_multiple_delimiters_splits_once():
    # only the first separator splits; the rest stays in the user part
    assert llm.split_system_user("A:-:-:-:B:-:-:-:C") == ("A", "B:-:-:-:C")


def test_split_system_user_none():
    assert llm.split_system_user(None) == ("", "")


def _run_standalone():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    if failures:
        print(f"\n{failures} test(s) failed")
        sys.exit(1)
    print("\nall provider_config tests passed")


if __name__ == "__main__":
    _run_standalone()
