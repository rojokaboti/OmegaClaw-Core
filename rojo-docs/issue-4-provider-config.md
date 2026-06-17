# Change Report — Issue #4: Config-Driven Provider/Model Selection

**Branch:** `feat/llm-provider-config` (off `main`, which has #1/#2/#3 merged)
**Issue:** #4 — "Move provider and model configuration out of hardcoded Python defaults"

---

## 1. Why this change exists

Provider/model selection was inconsistent and not reproducible without editing Python:

- `lib_llm_ext.py` hardcoded 7 provider registrations (name, API-key env, model,
  base_url) — switching model meant editing Python source.
- `src/loop.metta` `(configure LLM gpt-5.4)` was **dead code**: it was never passed to
  `callProvider` and never read anywhere, so the "LLM" value was *misleading* — with
  `provider=Anthropic` the real model was `claude-opus-4-6`, not `gpt-5.4`.
- Each provider parsed the `:-:-:-:` system/user separator differently (replace-with-space
  vs `split` vs conditional `split(...,1)`).

## 2. Before → after

| | Before | After |
|---|---|---|
| Provider/model source | hardcoded in `lib_llm_ext.py` | `profile/llm_providers.yaml` (+ `OMEGACLAW_LLM_CONFIG_PATH`) |
| Switch model/provider | edit Python | edit YAML / env, **no Python edit** |
| MeTTa `LLM` value | dead, hardcoded `gpt-5.4` (wrong) | reflects the real resolved model |
| Startup visibility | none | `[llm_config] provider=… model=… base_url=… class=… available=…` |
| `:-:-:-:` parsing | 3 different implementations | one `split_system_user()` helper |

## 3. Files changed

| File | Change |
|---|---|
| `src/provider_config.py` *(new)* | YAML loader (`OMEGACLAW_LLM_CONFIG_PATH`, relative→install root), `validate_config` (model/api_key_env/base_url/api_style/default_provider). **Failure model:** absent shipped default → fail-open to `_BUILTIN_DEFAULTS`; **explicit** path missing/invalid → fail **closed** (`FAIL_CLOSED` sentinel), opt back in with `OMEGACLAW_LLM_CONFIG_FAIL_OPEN=1`. `config_model`/`default_provider`. |
| `profile/llm_providers.yaml` *(new)* | The 6 real providers with exact current values (api_style + reasoning), mirroring the built-in defaults. |
| `lib_llm_ext.py` | `split_system_user()` (single delimiter parser; the 3 providers now share it, each keeping its send shape); config-driven registration loop replacing the 7 hardcoded lines (class chosen by `api_style`/`reasoning`; `TestProvider` always registered); OpenRouter reasoning from config; `effective_model()` + `describe_effective_config()`. |
| `src/loop.metta` | `LLM` reflects the real model via `(py-call (lib_llm_ext.effective_model (provider)))`; startup logs `describe_effective_config`. |
| `entrypoint.sh`, `scripts/omegaclaw` | Thread `OMEGACLAW_LLM_CONFIG_PATH`. |
| `README.md` | Env-var row + "Provider / model configuration" section. |
| `Autotests/test_provider_config.py` *(new)* | 25 tests (stub `openai`); loading/validation/relative-path/registration/split + fail-closed-on-explicit & fail-open opt-in. In `run_mandatory` + CI self-test. |
| `benchmarks/provider_config_*` *(new)* | Reproducibility matrix + deterministic results. |

## 4. KPI results (`benchmarks/provider_config_results.md`)

| Provider/model combo | resolved model | Python edit to switch |
|---|---|---|
| default (shipped YAML) `Anthropic` | `claude-opus-4-6` | yes → **no** |
| `OpenAI` | `gpt-5.4` | yes → **no** |
| `OpenRouter` | `z-ai/glm-5.1` | yes → **no** |
| custom model via `OMEGACLAW_LLM_CONFIG_PATH` `Anthropic` | `claude-test-override` | yes → **no** |

All 4 combos (including an env-selected custom YAML) resolve via config/env with **0 Python
edits**; the system/user split is normalized; the effective provider/model is visible in the
startup log. Default behavior is preserved (shipped YAML mirrors the old hardcoded values).

## 5. End-to-end validation (in-container)

- **Boot/readiness + startup log:** passed. The agent boots and logs the effective config.
  In the mock container (`-p Test`) the line correctly reads
  `[llm_config] provider=Test model=(n/a) base_url=(n/a) class=TestProvider available=True` —
  i.e. it reflects the *actual* active provider (proof that `describe_effective_config` reads
  the real registry, and that the `(configure LLM (py-call …))` line evaluated without error).
- **`effective_model` (config-driven) in-container:** `Anthropic → claude-opus-4-6`,
  `OpenAI → gpt-5.4`, `OpenRouter → z-ai/glm-5.1`.
- **Provider/model switch with no Python edit, in-container:** pointing
  `OMEGACLAW_LLM_CONFIG_PATH` at a custom YAML resolved the model to `in-container-override`.
- **In-container full mandatory suite (`pytest @run_mandatory`): 128 passed, 0 failed** — the
  config-driven registration keeps the `TestProvider` mock path working; no regression. (This
  is the whole Dockerized suite; the focused host file `Autotests/test_provider_config.py` is
  **25 tests**.)
- **Fail-closed proof (PR #23 review fix):** with an explicit-but-missing
  `OMEGACLAW_LLM_CONFIG_PATH`, registration registers **no external provider** (Anthropic/OpenAI
  absent, only `Test`) and logs `[provider_config] SECURITY …` + `[lib_llm_ext] SECURITY …`;
  `OMEGACLAW_LLM_CONFIG_FAIL_OPEN=1` restores the built-in fallback.

## 6. Reviewer guide — test & compare against the previous version

Prereqs: Python 3.12, `pytest`. Docker in the `docker` group for §E (else prefix with `sg docker -c "…"`).

### A. Read the core diff
```bash
git checkout feat/llm-provider-config
git diff main -- lib_llm_ext.py src/provider_config.py profile/llm_providers.yaml src/loop.metta
```
Focus on the config-driven registration loop, `split_system_user`, and the `LLM`/startup-log lines in `loop.metta`.

### B. Pure-Python checks (seconds, no Docker)
```bash
python3 src/provider_config.py                 # config self-tests
python3 Autotests/test_provider_config.py      # 21 unit tests (stubs openai)
python3 benchmarks/provider_config_benchmark.py    # reproducibility matrix; non-zero exit if it regresses
```

### C. Hand demo — switch model via config/env only (seconds)
```bash
python3 - <<'PY'
import sys, types, os, tempfile
sys.modules.setdefault("openai", types.ModuleType("openai")).OpenAI = object
sys.path[:0] = ["src", "."]
import provider_config as pc, lib_llm_ext as llm
print("default Anthropic model:", llm.effective_model("Anthropic"))
print("OpenAI model:", llm.effective_model("OpenAI"))
# switch Anthropic's model via a custom YAML — no Python edit:
y = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
y.write("version: 1\ndefault_provider: Anthropic\nproviders:\n  Anthropic: {api_key_env: ANTHROPIC_API_KEY, model: my-custom-model, base_url: u, api_style: chat_completions}\n"); y.close()
os.environ["OMEGACLAW_LLM_CONFIG_PATH"]=y.name; pc.reset_cache()
print("after env override:", pc.config_model("Anthropic", pc.load_config()))
PY
```
Expected: `claude-opus-4-6`, `gpt-5.4`, then `my-custom-model` — all without editing Python.

### D. In-container (Docker)
```bash
docker build -t omegaclaw:local .
TEST_SERVER_IP=host.docker.internal IMPORT_KB_ON_START=0 ./scripts/omegaclaw start -p Test -t test -d omegaclaw:local
docker logs omegaclaw 2>&1 | grep "\[llm_config\]"      # startup effective config line
# resolve a different provider's model with no Python edit:
docker exec -i omegaclaw python3 -c "import sys; sys.path.insert(0,'/PeTTa/repos/OmegaClaw-Core'); import lib_llm_ext as l; print(l.effective_model('OpenAI'))"
./scripts/omegaclaw stop
```

### E. Compare to `main`
```bash
git show main:lib_llm_ext.py | grep -n "_register_provider("   # pre-refactor hardcoded registrations
git show main:src/loop.metta | sed -n '16p'                    # the dead `(configure LLM gpt-5.4)`
git diff main --stat
```

## 7. Risk / rollback
- Behavior preserved: shipped YAML mirrors the old hardcoded values; `AIProvider` send shape
  kept byte-identical; `callProvider` unchanged; the `Test` provider (mock) is always registered.
- **Failure model (PR #23 review fix):** an *absent shipped default* fails open to built-in
  defaults (out-of-box availability); an *explicitly supplied* `OMEGACLAW_LLM_CONFIG_PATH` that
  is missing/invalid fails **closed** (no external provider registered, loud `SECURITY` log) so
  prompts can't be silently routed to a built-in cloud default. `OMEGACLAW_LLM_CONFIG_FAIL_OPEN=1`
  opts an explicit config back into fallback. A relative path resolves against the install root
  (#2 lesson). Switching providers is still YAML/env-only (the KPI).
- No deferrals — the issue scope is fully implemented.
