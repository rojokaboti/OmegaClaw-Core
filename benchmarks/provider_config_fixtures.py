"""Fixtures for the provider-config reproducibility benchmark (Issue #4).

COMBOS: provider/model combinations to resolve via config/env only.
SPLIT_CASES: prompts to run through the normalized system/user splitter.
"""

# (label, env-overrides, provider-to-resolve, expected-model)
COMBOS = [
    ("default (shipped YAML)", {}, "Anthropic", "claude-opus-4-6"),
    ("switch provider -> OpenAI", {}, "OpenAI", "gpt-5.4"),
    ("switch provider -> OpenRouter", {}, "OpenRouter", "z-ai/glm-5.1"),
    # A custom YAML selected via env, overriding Anthropic's model — no Python edit.
    ("custom model via OMEGACLAW_LLM_CONFIG_PATH", {"_custom_yaml": True}, "Anthropic", "claude-test-override"),
]

CUSTOM_YAML = (
    "version: 1\n"
    "default_provider: Anthropic\n"
    "providers:\n"
    "  Anthropic: {api_key_env: ANTHROPIC_API_KEY, model: claude-test-override, "
    "base_url: https://api.anthropic.com/v1/, api_style: chat_completions}\n"
)

SPLIT_CASES = [
    ("system + user", "SYSTEM PROMPT:-:-:-:HUMAN-MSG: hello", ("SYSTEM PROMPT", "HUMAN-MSG: hello")),
    ("no separator", "just a user message", ("", "just a user message")),
]
