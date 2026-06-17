# Provider/Model Config Reproducibility Benchmark — Issue #4

- **baseline** (`main`): provider/model hardcoded in `lib_llm_ext.py` — switching requires a **Python edit**.
- **candidate**: provider/model in `profile/llm_providers.yaml` (or `OMEGACLAW_LLM_CONFIG_PATH`) — switching is **config/env only**.

| Provider/model combo | resolved model | Python edit to switch (baseline → candidate) |
| --- | --- | --- |
| default (shipped YAML) (`Anthropic`) | `claude-opus-4-6` ✓ | yes → **no** |
| switch provider -> OpenAI (`OpenAI`) | `gpt-5.4` ✓ | yes → **no** |
| switch provider -> OpenRouter (`OpenRouter`) | `z-ai/glm-5.1` ✓ | yes → **no** |
| custom model via OMEGACLAW_LLM_CONFIG_PATH (`Anthropic`) | `claude-test-override` ✓ | yes → **no** |

Normalized system/user split (single parser, all providers):

| case | (system, user) |
| --- | --- |
| system + user | `('SYSTEM PROMPT', 'HUMAN-MSG: hello')` ✓ |
| no separator | `('', 'just a user message')` ✓ |

Startup config log (effective provider/model visible): `[llm_config] provider=Anthropic model=claude-opus-4-6 base_url=https://api.anthropic.com/v1/ class=AIProvider available=False`

Reproduce: `python3 benchmarks/provider_config_benchmark.py`
