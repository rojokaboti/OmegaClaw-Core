# Reasoning-Trace KPI Benchmark — Issue #7

Fixture dataset: **12 scripted loop iterations** (`reasoning_trace_fixtures.FIXTURES`) driven through the real `src/tracing.py`, covering normal/multi actions, a parse error, and policy denials.

- **baseline** = original text logs with isolated per-call ids (no shared cross-phase id).
- **candidate** = structured JSONL traces: one `trace_id` links every event of an iteration.

| Metric | baseline | candidate |
| --- | --- | --- |
| Trace-id coverage (event has trace_id) | 0.00 | 1.00 |
| **Full-linkage rate** (input→llm→parse→result share id) | **0.00** | **1.00** |
| JSONL-parseable events | 0.00 | 1.00 |
| Summary metrics (errors/denials/types/latency) | False | True |

### Trace summary (candidate, from `scripts/omegaclaw-trace-summary`)

parse_errors=2 · invalid_actions=2 · policy_denials=2 · avg_llm_latency_ms=139.17 · actions_by_type={'send': 3, 'metta': 1, 'remember': 1, 'shell': 2, 'search': 1, 'write-file': 1, 'pin': 1, 'query-claims': 1}

The candidate links **100%** of iterations end-to-end and the file is fully JSON-parseable, so the summary tool reports parse errors, policy denials, action mix, and latency. The baseline's isolated per-call ids cannot link an input to its resulting action.

Reproduce: `python3 benchmarks/reasoning_trace_benchmark.py`
