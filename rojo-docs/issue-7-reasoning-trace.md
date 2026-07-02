# Change Report — Issue #7: Structured Reasoning Trace Logs

**Branch:** `feat/reasoning-trace` (off `main`, which has #1–#5 merged)
**Issue:** #7 — "Add structured reasoning trace logs for auditability and benchmark analysis"

---

## 1. Why this change exists

OmegaClaw runs a MeTTa reasoning/action loop, but its logs are text-oriented and there was **no
trace id linking a single iteration's input → LLM call → action parse → policy decision → result**.
Debugging a bad decision or auditing a benchmark meant grepping unlinked text. Issue #7 adds
first-class **JSONL reasoning traces**: every loop iteration mints one `trace_id`, and each phase
emits a structured event under it, so a run is line-by-line analyzable.

### Accuracy corrections (issue spec vs. reality)
- The loop is **MeTTa** (`src/loop.metta`), not Python — so trace emission uses a Python
  `contextvars` **current-trace context** set once per iteration via `py-call`, read implicitly by
  the Python components. No MeTTa signature churn, no threading the id through every call.
- **`session_id`/`turn_id` didn't exist.** `session_id` is minted once per process
  (`begin_session`); `turn_id`/`input_state_hash` are optional and set by producers via
  `set_context(...)` (the FreeCiv runner will call it — see §6).
- **Privacy was underspecified.** Traces can contain prompt/state/result text, so this reuses
  Issue #3's `redact_secrets` and defaults to **metadata/hashes only**; bodies are emitted only
  under a debug gate. (This is why `redact_secrets` was extracted — see §3.)

## 2. Before → after

| | Before | After |
|---|---|---|
| Per-iteration id | none (only an ephemeral per-LLM-call `uuid` in `_log_raw`) | one `trace_id` shared by every event of the iteration |
| Linkage input→…→result | not possible | 100% (all phases carry the same `trace_id`/`session_id`/`iteration`) |
| Format | free text on stdout | JSONL, one event per line, benchmark-analyzable |
| Privacy | n/a | metadata/hashes by default; redacted bodies only under a debug gate |
| Aggregation | none | `scripts/omegaclaw-trace-summary` (errors, denials, action mix, latency, linkage) |

## 3. Files changed

| File | Change |
|---|---|
| `src/tracing.py` *(new)* | Core: `contextvars` trace context (`begin_session`/`begin_iteration`/`set_context`/`end_iteration`), `emit()` JSONL writer (metadata-only default, redacted bodies under `OMEGACLAW_TRACE_BODIES`/`OMEGACLAW_DEBUG_LLM_RAW`, best-effort IO), typed events `trace_llm`/`trace_parse`/`trace_policy`/`trace_error`/`trace_result`. Default path `memory/traces/YYYYMMDD.jsonl`; `OMEGACLAW_TRACE_DISABLE` silences. Self-test. |
| `src/redaction.py` *(new)* | Extracted `redact_secrets` + patterns from `lib_llm_ext.py` (stdlib-only) so `tracing` reuses it without importing `openai`. Self-test. |
| `lib_llm_ext.py` | `redact_secrets`/`_REDACTION_PATTERNS` now imported+re-exported from `redaction` (backward-compatible for #3's tests); `callProvider` wraps `.chat()` with `trace_llm` (prompt hash, response metadata, latency). |
| `src/action_protocol.py` | `parse_and_render_metta` emits `action_parse`; `authorize_actions` emits `policy_decision` for env-disabled denials. Best-effort, never breaks the pipeline. |
| `src/tool_policy.py` | `log_denial` emits a `policy_decision` (denied) trace. |
| `src/loop.metta` | Two `py-call` hooks: `tracing.begin_session` (init), `tracing.begin_iteration $k` (per iteration), `tracing.end_iteration (repr $results)` (after results). |
| `lib_omegaclaw.metta` | Register `./src/tracing.py`. |
| `scripts/omegaclaw-trace-summary` *(new)* | Stdlib Python aggregator: events by phase, parse errors, invalid actions, policy denials, actions-by-type, avg LLM latency, and % fully-linked iterations. `--json`. |
| `benchmarks/reasoning_trace_{fixtures,benchmark}.py` + `_results.{md,json}` *(new)* | KPI A/B (baseline text logs vs candidate traces) with a `sys.exit(1)` gate; committed results. |
| `Autotests/test_tracing.py` *(new)* + `Autotests/run_mandatory` | 7 host tests (schema/linkage, privacy default + redaction, disable gate, pipeline hooks, summary aggregator); wired into the mandatory suite. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../src/redaction.py` + `python ../src/tracing.py`. |

## 4. KPI results (`benchmarks/reasoning_trace_results.md`)

12 scripted loop iterations driven through the real `src/tracing.py` (normal/multi actions, a parse
error, policy denials):

| Metric | baseline | candidate |
|---|---|---|
| Trace-id coverage (event has trace_id) | 0.00 | **1.00** |
| **Full-linkage rate** (input→llm→parse→result share id) | **0.00** | **1.00** |
| JSONL-parseable events | 0.00 | **1.00** |
| Summary metrics (errors/denials/types/latency) | False | **True** |

Summary (candidate): parse_errors=2 · invalid_actions=2 · policy_denials=2 · avg_llm_latency_ms≈139 ·
action mix across send/metta/shell/write-file/etc. Gate asserts coverage/linkage == 1.0 and
candidate strictly beats baseline; exits non-zero on regression. Structural results are identical
across runs (ids/timestamps are not part of the asserted output).

## 5. End-to-end validation
- `python3 src/redaction.py` / `python3 src/tracing.py` → self-tests pass.
- `python3 Autotests/test_tracing.py` → 7/7; mandatory pure suite **106 passed, 6 skipped**
  (chroma-backed memory tests skip on host). Existing `test_llm_logging.py` still passes with the
  extracted redactor.
- `python3 benchmarks/reasoning_trace_benchmark.py` → `KPI GATE: PASSED`.
- `python3 scripts/omegaclaw-trace-summary <trace>.jsonl` (+ `--json`) → correct aggregates.
- **In-container (documented, needs PeTTa):** run the real MeTTa loop ≥10 iterations; confirm each
  iteration emits linked JSONL (`iteration_start`→`llm_call`→`action_parse`[→`policy_decision`]→
  `iteration_end`) and the summary reports coverage/denials/latency.

## 6. Deferred
- **FreeCiv trace context wiring:** calling `tracing.set_context(turn_id=…, state_hash=…, session_id=…)`
  in `benchmarks/freeciv/live_play.py` / `llm_play.py`. Those files live on the Issue #6 branch, not
  `main`, so the call sites land when #6 + #7 converge. The `set_context` API ships now; the
  one-line integration is: after fetching a state, `tracing.set_context(turn_id=state["turn"],
  state_hash=adapter.state_hash(state))`.

## 7. Reviewer guide — test & compare against the previous version

### A. Read the core diff (no build)
```bash
git checkout feat/reasoning-trace
git diff main --stat
git diff main -- src/loop.metta lib_llm_ext.py src/action_protocol.py
```

### B. Pure-Python checks (seconds, no Docker)
```bash
python3 src/redaction.py            # redaction self-tests passed
python3 src/tracing.py              # tracing self-tests passed
python3 benchmarks/reasoning_trace_benchmark.py     # KPI GATE: PASSED
python3 Autotests/test_tracing.py   # 7/7 standalone
python3 src/action_protocol.py      # guard: pipeline hooks didn't break parsing
```

### C. Hand demo — linked trace + summary (seconds)
```bash
python3 - <<'PY'
import os, sys, tempfile; sys.path.insert(0, "src")
d = tempfile.mkdtemp(); os.environ["OMEGACLAW_TRACE_PATH"] = d + "/t.jsonl"
import tracing, action_protocol as ap
tracing.begin_session(); tracing.begin_iteration(1, input_text="PROMPT: demo")
ap.parse_and_render_metta('{"actions":[{"tool":"send","args":{"text":"hi"}}]}')
tracing.end_iteration("done")
print(open(os.environ["OMEGACLAW_TRACE_PATH"]).read())
PY
python3 scripts/omegaclaw-trace-summary "$(ls -t memory/traces/*.jsonl 2>/dev/null | head -1)" || true
```

### D. In-container (Docker)
```bash
docker build -t omegaclaw:local .
# drive the loop; then inside the container:
#   python3 scripts/omegaclaw-trace-summary memory/traces/$(date -u +%Y%m%d).jsonl
```

### E. Compare to `main`
```bash
git show main:src/tracing.py             # does not exist on main
git ls-tree main -- src/redaction.py     # absent on main
git diff main --stat
```

## 8. Risk / rollback
- Additive + privacy-safe: metadata-only default, `redact_secrets`, best-effort IO — a trace failure
  never breaks the loop. `OMEGACLAW_TRACE_DISABLE=1` fully silences.
- `redaction.py` extraction keeps `lib_llm_ext.redact_secrets` importable (re-export) so #3's tests
  pass unchanged.
- Hooks are wrap-only at existing choke points (`callProvider`, `parse_and_render_metta`,
  `authorize_actions`/`log_denial`) — no behavior change to LLM output, parsing, or policy decisions.
- Branch overlaps the open PRs #26 (freeciv) / #27 (sync) on `loop.metta`/`action_protocol.py`/
  `lib_llm_ext.py`; whichever merges later needs a small mechanical conflict resolution.
- Not pushed until ready; open a PR against `rojokaboti/OmegaClaw-Core`.
