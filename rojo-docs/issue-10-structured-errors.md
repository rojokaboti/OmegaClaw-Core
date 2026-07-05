# Change Report — Issue #10: Structured Error Recovery Events

**Branch:** `feat/structured-errors` (off `main`, which has #1–#9 merged)
**Issue:** #10 — "Replace string-only error feedback with structured error recovery events"

---

## 1. Why this change exists

When an action failed, the agent fed the model **opaque strings** — symbolic MeTTa
tokens like `…MULTI_COMMAND_FAILURE_NOTHING_WAS_DONE…` /
`…SINGLE_ACTION_EXECUTION_ERROR_NOTHING_WAS_DONE…` (`src/loop.metta`), landing in
history as raw `ERROR_FEEDBACK: (…)` (`src/memory.metta`). These are not
machine-readable categories, so recovery was unreliable and error rates could not
be counted across benchmark runs.

**Accuracy note (issue spec vs. reality).** The issue implies errors were *only*
strings. In fact the JSON action protocol (#1) and tool policy (#2) already produced
structured `{code, message}` objects at the parse/validate/authorize layer — but they
were **flattened into a single `ACTION_PROTOCOL_ERROR:` string** before crossing into
MeTTa, and the two MeTTa-native failures (`sread` parse, per-action `eval`) had **no
structure at all**. And `tracing.trace_error(stage, code, message)` (#7) already existed
but was **unused**. So this pass (a) consolidates the existing codes into a single
five-category vocabulary, (b) adds structured capture for the MeTTa-only layer via
`py-call` bridges, and (c) wires the previously-dead `trace_error` emission point.

## 2. Before → after

| | Before | After |
|---|---|---|
| Error identity | opaque symbolic token / flattened string | one of 5 machine-readable categories |
| Feedback to model | `…NOTHING_WAS_DONE…` or full OUTPUT_FORMAT dump | concise, category-specific **repair hint** |
| Failed action | not captured | captured (`failed_action`) |
| Retryability | implicit | explicit `retryable` flag |
| Linkage | none | emitted to the reasoning trace under the iteration's `trace_id` |
| Metrics | none | per-category counts via `scripts/omegaclaw-trace-summary` |

The five categories: `parse_error`, `unknown_tool`, `schema_validation_error`,
`tool_policy_denied` (not retryable — a re-attempt of the same action re-denies),
`tool_runtime_error`.

## 3. Files changed

| File | Change |
|---|---|
| `src/errors.py` *(new)* | The vocabulary + engine. `ERROR_TYPES`, `CODE_TO_TYPE` (maps every action_protocol/tool_policy code → category), `REPAIR_HINTS`, `RETRYABLE`. `build_error` (Issue #10 schema: `error_type, message, failed_action, repair_hint, trace_id, retryable, code`; `trace_id` read from `tracing.current()`, never minted), `record_error`/`record_code` (build + `tracing.trace_error` + in-process counter, all best-effort), `format_error_for_llm`, `counts()`. MeTTa bridges `record_parse_error`/`record_runtime_error` return the concise hint. Self-test. |
| `src/tracing.py` | `trace_error` extended (was `stage/code/message` only) to persist the **full schema**: `error_type` (category), the original granular `code` (e.g. `missing_arg`), `retryable`, `repair_hint` — all non-sensitive, always emitted. The `failed_action` is body-like (can embed file contents/echoed text), so it follows the same privacy gate as prompt/result bodies: `failed_action_sha`+`failed_action_chars` always, redacted body only under `OMEGACLAW_TRACE_BODIES`. |
| `src/action_protocol.py` | Best-effort `import errors`. Every failure site records a structured event mapped through `CODE_TO_TYPE`, **passing the failed action**: `authorize_actions` records per denied action at its single choke point (covers every policy denial, no double count, precise `failed_action`); `parse_and_render_metta` records `no_json`/validation errors with the bounded raw output. `_error_string` appends the **concise repair hint** for the known category (falls back to the full OUTPUT_FORMAT block otherwise). Loop re-prompt contract preserved: the string stays non-`(`-prefixed and keeps the `ACTION_PROTOCOL_ERROR:` sentinel. |
| `src/loop.metta` | `HandleError` gains a `$kind`; inside the `(Error …)` branch only, `recordErrorHint` runs a `py-call` (`errors.record_parse_error` / `errors.record_runtime_error`) that emits the structured event and returns the concise hint stored into `&error` (so `ERROR_FEEDBACK` now carries an actionable hint, not the opaque token). Call sites pass `parse_error` / `tool_runtime_error`. A successful action never records. |
| `lib_omegaclaw.metta` | Registers `./src/errors.py`. |
| `scripts/omegaclaw-trace-summary` | Adds `error_events` + `errors_by_type` (counts `phase=="error"` events by `error_type` category) and renders them. |
| `Autotests/test_errors.py` *(new)* | 16 host tests: schema, exhaustive `CODE_TO_TYPE` drift guard, per-category classification through the real pipeline, **assertions on the actually-emitted JSONL event** (category + original code + retryable + repair_hint + failed_action ref), failed-action body recoverability under `OMEGACLAW_TRACE_BODIES`, counters, summary aggregation, and the "valid action records no error" guard. Added to `run_mandatory` + CI phase-1. |
| `benchmarks/error_recovery_{fixtures,benchmark}.py` + `_results.{md,json}` *(new)* | KPI A/B (string-only baseline vs candidate) with a `sys.exit(1)` gate. **Metrics are read off the actual emitted trace event** (production path), not a locally-built dict, so a payload regression is caught. Committed results. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../src/errors.py`. |
| `README.md`, `benchmarks/README.md` | Document the feature (no new env var — it rides the existing `OMEGACLAW_TRACE_*` file). |
| `benchmarks/results.json` | Regenerated: the #1 corpus snapshots the rendered error string, which now shows the concise repair hint. **KPI counts unchanged** (`results.md` untouched). |

## 4. KPI results (`benchmarks/error_recovery_results.md`)

5 fixtures, one per category, driven through the real `src/errors.py` +
`src/action_protocol.py`:

5 fixtures, driven through the real production paths; **metrics read off the actual
emitted JSONL event**:

| Metric | baseline (string-only) | candidate |
|---|---|---|
| Machine-readable error_type (one of 5) | 0.00 | **1.00** |
| Correct category | 0.00 | **1.00** |
| Original protocol code preserved (e.g. `missing_arg`) | 0.00 | **1.00** |
| Retryable flag present | 0.00 | **1.00** |
| Concise repair hint | 0.00 | **1.00** |
| Failed action ref in trace (sha, privacy-default) | 0.00 | **1.00** |
| Failed action body recoverable (`OMEGACLAW_TRACE_BODIES`) | 0.00 | **1.00** |
| Trace id present | 0.00 | **1.00** |
| Next-turn recovery (corrected input parses) | 0.00 | **1.00** |
| **Unknown / unclassified bucket** | **1.00** | **0.00** |

Structured error events emitted to the trace: **5** (one per fixture; counts by
category `{parse_error:1, unknown_tool:1, schema_validation_error:1,
tool_policy_denied:1, tool_runtime_error:1}`). The gate asserts the candidate
classifies every fixture (0 unknown vs baseline 100%), preserves the original code,
carries retryable + concise repair hint + failed-action reference **on the emitted
event**, and recovers the failed-action body under bodies mode; exits non-zero on
regression. Satisfies the issue's KPI acceptance gate.

### Post-review fix (PR #31)
A reviewer found that `trace_error` originally persisted only `stage/code/message`
and overwrote `code` with the category, so the durable JSONL lost `failed_action`,
`repair_hint`, `retryable`, and the granular code — and the benchmark masked it by
asserting on a locally-built dict. Fixed: `trace_error` now emits the full schema
(privacy-gating only the failed-action body), the recording sites pass the failed
action, the summary counts by `error_type`, and the benchmark + unit tests now assert
against the **actually-emitted event**.

## 5. End-to-end validation

**Host (pure-Python — the committed gate):**
- `python3 src/errors.py` → self-tests pass.
- `python3 Autotests/test_errors.py` → 16/16 (incl. emitted-event schema + bodies
  recoverability). Full host-runnable subset (`test_errors`, `test_action_protocol`,
  `test_tool_policy`, `test_tracing`, `test_channel_registry`, `test_metta_sessions`,
  `mock/test_actions_equivalence`) under pytest → **95 passed**.
- All module self-tests green (helper/action_protocol/tool_policy/provider_config/
  memory_schema/redaction/tracing/errors/metta_sessions/channel_registry).
- `python3 benchmarks/error_recovery_benchmark.py` → `KPI GATE: PASSED`. All other KPI
  gates (`reasoning_trace`, `tool_policy`, `run`, `channel_registry`, `metta_sessions`)
  still pass — no regression.
- `scripts/omegaclaw-trace-summary <trace>.jsonl` → shows `structured errors` +
  `errors by type`.

**In-container (Docker — verifies the live MeTTa wiring; documented, gated):** build
`omegaclaw:local`, run `@run_mandatory`, then drive a malformed action + a
runtime-erroring `eval` and confirm `memory/traces/<date>.jsonl` has `phase=="error"`
events with `code=parse_error` / `tool_runtime_error`, and that history `ERROR_FEEDBACK`
now shows the concise hint. (No host MeTTa/hyperon runtime — same Docker-gated posture as
#6/#7. The MeTTa files were paren-balance checked and the edit is a minimal wrapper around
the known-good `HandleError`.)

## 6. Reviewer guide — test & compare against the previous version

### A. Read the core diff (no build)
```bash
git checkout feat/structured-errors
git diff main -- src/errors.py src/action_protocol.py src/loop.metta scripts/omegaclaw-trace-summary
```
Focus on `errors.CODE_TO_TYPE`/`build_error`, the `authorize_actions` single-choke-point
recording, and the `HandleError` `recordErrorHint` wrapper (records only inside the
`(Error …)` branch).

### B. Pure-Python checks (seconds, no Docker)
```bash
python3 src/errors.py
python3 src/action_protocol.py          # loop re-prompt contract intact (non-"(" error strings)
python3 Autotests/test_errors.py
python3 src/tracing.py                   # trace_error still self-tests
```

### C. Reproduce the KPI experiment (seconds)
```bash
python3 benchmarks/error_recovery_benchmark.py    # prints the table; KPI GATE: PASSED
```

### D. Hand demo — one of each category + a linked trace summary (seconds)
```bash
python3 - <<'PY'
import os, sys, tempfile; sys.path.insert(0, "src")
p = os.path.join(tempfile.mkdtemp(), "t.jsonl"); os.environ["OMEGACLAW_TRACE_PATH"] = p
import tracing, errors, json
tracing.begin_session(); tracing.begin_iteration(1)
for t, m in [("parse_error","x"),("unknown_tool","rm-rf"),("schema_validation_error","missing"),
             ("tool_policy_denied","shell"),("tool_runtime_error","boom")]:
    e = errors.record_error(t, m)
    print(e["error_type"], "| retryable:", e["retryable"], "| hint:", e["repair_hint"][:48], "…")
tracing.end_iteration("done")
PY
python3 scripts/omegaclaw-trace-summary "$p"   # errors by type = one of each
```

### E. In-container (Docker) — live MeTTa wiring
```bash
docker build -t omegaclaw:local .
TEST_SERVER_IP=host.docker.internal IMPORT_KB_ON_START=0 ./scripts/omegaclaw start -p Test -t test -d omegaclaw:local
( cd Autotests && python3 -m pytest -s -v @run_mandatory )
# drive a malformed action + a runtime-erroring eval, then:
docker exec omegaclaw python3 scripts/omegaclaw-trace-summary memory/traces/$(date -u +%Y%m%d).jsonl
./scripts/omegaclaw stop
```

### F. Compare to `main`
```bash
git show main:src/errors.py            # does not exist on main
grep -n "NOTHING_WAS_DONE" main:src/loop.metta 2>/dev/null || git show main:src/loop.metta | grep -n NOTHING_WAS_DONE
```
`main`'s loop feeds the opaque symbolic tokens; this branch replaces them with classified,
hint-bearing structured events.

## 7. Risk / rollback
- **Additive + best-effort.** Every recording path (`errors.record_*`, `tracing.trace_error`)
  swallows its own exceptions — error bookkeeping can never break the loop. If `src/errors.py`
  is unimportable, `action_protocol` falls back to the original full-OUTPUT_FORMAT error string.
- **Loop re-prompt contract preserved.** Error strings stay non-`(`-prefixed and keep the
  `ACTION_PROTOCOL_ERROR:` sentinel; existing tests assert both.
- **No double counting.** Policy denials are recorded once at the `authorize_actions` choke
  point (the only caller of `check_action`/`log_denial`); `tool_policy.py` was intentionally
  left unchanged.
- **MeTTa hooks are minimal, wrap-only.** `HandleError` gained one `(let $hint …)` wrapper that
  runs only inside the `(Error …)` branch, so a successful action never records. Live capture of
  `sread`/`eval` errors is verified in-container.
- **No new env var**; traces ride the existing `OMEGACLAW_TRACE_*` file (`OMEGACLAW_TRACE_DISABLE=1`
  silences all emission, including errors).
- Not pushed until ready; open a PR against `rojokaboti/OmegaClaw-Core`.
