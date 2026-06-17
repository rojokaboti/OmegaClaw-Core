# Change Report — Issue #3: Gate & Redact Raw LLM Response Logging

**Branch:** `feat/llm-log-redaction` (off `main`, which has #1 and #2 merged)
**Issue:** #3 — "Gate and redact raw LLM response logging"

---

## 1. Why this change exists

`lib_llm_ext._log_raw()` printed the **full raw model response** on every reply:

```python
print(f"[LLM_RAW] ... raw={raw!r}")   # unconditional
```

That output goes to stdout → `docker logs`. Raw model output routinely contains
private user text, tool results, and sometimes **echoed secrets** (a user pastes a
token, the model repeats it). Logging it unconditionally is a privacy/security leak.
`_log_raw` is the single sink for all real providers (AIProvider, AsiOneProvider,
OpenAIProvider; OpenRouter via `super().chat()`), so the leak was on every path.

## 2. Before → after

**Before:** `[LLM_RAW] ts=… provider=… model=… chars=… raw='<entire response, secrets and all>'` — always.

**After (default, no env):** metadata only, no body:
```
[LLM_RAW] ts=… trace=ab12cd34 provider=OpenAI model=… chars=1234 raw=<redacted; set OMEGACLAW_DEBUG_LLM_RAW=1>
```
**After (`OMEGACLAW_DEBUG_LLM_RAW=1`):** raw context shown, but every secret-shaped
value is replaced with `[REDACTED:<kind>]` first — unredacted secrets are never emitted.
Optional `OMEGACLAW_LLM_LOG_PATH` appends JSONL records (metadata always; redacted raw
only in debug).

The whole change is localized to `_log_raw` + a new `redact_secrets()`; callers are
untouched, so every provider benefits at once.

## 3. Files changed

| File | Change |
|---|---|
| `lib_llm_ext.py` | New `redact_secrets()` (OpenAI/Anthropic `sk-…`, GitHub `ghp_`/`github_pat_`, AWS `AKIA…`, `Bearer …`, long base64 → `[REDACTED:<kind>]`); rewritten `_log_raw()` (metadata + short trace id by default; redacted raw only when `OMEGACLAW_DEBUG_LLM_RAW` is truthy; optional JSONL via `OMEGACLAW_LLM_LOG_PATH`, best-effort). |
| `entrypoint.sh`, `scripts/omegaclaw` | Thread `OMEGACLAW_DEBUG_LLM_RAW` + `OMEGACLAW_LLM_LOG_PATH` through the env scrub + `docker run -e`. |
| `README.md` | Env-var rows + "Debugging LLM responses (privacy)" section. |
| `Autotests/test_llm_logging.py` *(new)* | 11 tests (stubs `openai`); default/debug/redaction/JSONL. Added to `run_mandatory`. |
| `benchmarks/llm_logging_{fixtures,benchmark}.py` + `llm_logging_results.{md,json}` *(new)* | Baseline-vs-default-vs-debug leakage matrix. |

## 4. KPI results (`benchmarks/llm_logging_results.md`)

One secret-bearing response (GitHub token + PAT, OpenAI/Anthropic keys, bearer, AWS,
long base64) logged under each config:

| Metric | baseline (pre-fix) | default | debug |
|---|---|---|---|
| Raw body in log | yes | **no** | yes |
| **Unredacted secret leaks** | **7** | **0** | **0** |
| Metadata present | yes | yes | yes |

**Headline:** the pre-fix logger leaked all 7 secret-shaped strings; the new default
leaks **0** and logs no raw body (metadata retained); debug shows raw context with **0**
unredacted secrets.

## 5. End-to-end validation (in-container)

The mock `TestProvider` does not call `_log_raw` (only real providers do), so `_log_raw`
is exercised in-container by probing the **real baked code** via `docker exec`
(`openai` is installed there).

- **Agent boot/readiness (`CHARS_SENT`):** passed — the modified `lib_llm_ext` imports and
  the agent loop runs.
- **`_log_raw` probe (real baked code) on a secret-bearing string:**

  | Mode | Output |
  |---|---|
  | default | `… chars=122 raw=<redacted; set OMEGACLAW_DEBUG_LLM_RAW=1>` — no secret |
  | `OMEGACLAW_DEBUG_LLM_RAW=1` | `… raw='Here is the summary. github=[REDACTED:github_token] openai=[REDACTED:openai_key] Bearer [REDACTED:bearer]'` |

- **`@run_mandatory`: 108 passed, 0 failed** — no regression (the mock `TestProvider` path is
  unaffected; the new `test_llm_logging.py` runs in the suite).

## 6. Reviewer guide — test & compare against the previous version

Prereqs: Python 3.12, `pytest` (host). Docker in the `docker` group for §E (else prefix with `sg docker -c "…"`).

### A. Read the core diff
```bash
git checkout feat/llm-log-redaction
git diff main -- lib_llm_ext.py
```
Focus on `redact_secrets()` and the rewritten `_log_raw()`.

### B. Pure-Python checks (seconds, no Docker)
```bash
python3 Autotests/test_llm_logging.py     # 11 tests (stubs openai)
python3 benchmarks/llm_logging_benchmark.py   # prints the leakage matrix; non-zero exit if it regresses
```

### C. Hand demo — default vs debug (seconds)
```bash
python3 - <<'PY'
import sys, types, os
sys.modules.setdefault("openai", types.ModuleType("openai")).OpenAI = object
sys.path.insert(0, ".")
import lib_llm_ext as llm
resp = "summary ... github=ghp_A1b2C3d4E5f6G7h8I9j0KLMNOP key=sk-A1b2C3d4E5f6G7h8I9j0K1l2"
print("--- default ---");                 llm._log_raw("OpenAI","gpt",resp)
os.environ["OMEGACLAW_DEBUG_LLM_RAW"]="1"; print("--- debug ---"); llm._log_raw("OpenAI","gpt",resp)
PY
```
Expected: default → `raw=<redacted; …>` (no token); debug → raw text but `ghp_…`/`sk-…` shown as `[REDACTED:…]`.

### D. In-container probe (Docker)
```bash
docker build -t omegaclaw:local .
cat > /tmp/probe.py <<'PY'
import sys; sys.path.insert(0,"/PeTTa/repos/OmegaClaw-Core")
import lib_llm_ext as llm
llm._log_raw("OpenAI","gpt","github=ghp_A1b2C3d4E5f6G7h8I9j0KLMNOP normal text")
PY
docker run --rm -i --entrypoint python3 omegaclaw:local - < /tmp/probe.py                         # default: redacted
docker run --rm -i -e OMEGACLAW_DEBUG_LLM_RAW=1 --entrypoint python3 omegaclaw:local - < /tmp/probe.py  # debug: [REDACTED:github_token]
```

### E. Compare to `main`
```bash
git show main:lib_llm_ext.py | sed -n '5,7p'   # pre-fix: raw={raw!r} unconditional
git diff main --stat
```

## 7. Risk / rollback
- Behavior change is intentional (default no longer prints raw bodies). Nothing consumes
  the `LLM_RAW` line (CI readiness uses `CHARS_SENT`).
- Redaction is conservative (typed placeholders); debug remains useful. Even debug never
  prints unredacted secrets (acceptance criterion).
- JSONL write is best-effort and never raises into the provider `chat()` path.
- No deferrals — the issue scope is fully implemented.
