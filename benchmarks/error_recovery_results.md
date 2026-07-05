# Error-Recovery KPI Benchmark — Issue #10

Fixture dataset: **5 fixtures**, one per canonical error category (`error_recovery_fixtures.FIXTURES`), driven through the real production paths (`action_protocol.parse_and_render_metta` and `errors.record_runtime_error`).

**Metrics are read off the actual JSONL event emitted to the durable trace** (`phase=="error"`), not a locally-built dict — so a regression in the persisted payload is caught here.

- **baseline** = original `asi-alliance` string-only feedback (`…NOTHING_WAS_DONE…`): no category, code, failed action, retryability, repair hint, or trace id — one unknown bucket.
- **candidate** = structured error events with the full schema under the iteration's `trace_id`.

| Metric | baseline | candidate |
| --- | --- | --- |
| Machine-readable error_type (one of 5) | 0.00 | 1.00 |
| Correct category | 0.00 | 1.00 |
| Original protocol code preserved (e.g. missing_arg) | 0.00 | 1.00 |
| Retryable flag present | 0.00 | 1.00 |
| Concise repair hint | 0.00 | 1.00 |
| Failed action ref in trace (sha, privacy-default) | 0.00 | 1.00 |
| Failed action body recoverable (OMEGACLAW_TRACE_BODIES) | 0.00 | 1.00 |
| Trace id present | 0.00 | 1.00 |
| Next-turn recovery (corrected input parses) | 0.00 | 1.00 |
| Unknown / unclassified bucket | 1.00 | 0.00 |

Structured error events emitted to the trace: **5** for 5 fixtures in default mode (counts by category: {'parse_error': 2, 'unknown_tool': 2, 'schema_validation_error': 2, 'tool_policy_denied': 2, 'tool_runtime_error': 2}).

The emitted event carries the machine-readable **category**, the **original granular code** (so downstream analytics can recover both the classification and the exact protocol failure), the **retryable** flag, a concise **repair hint**, and a **failed-action reference** (`failed_action_sha` always; the redacted body under `OMEGACLAW_TRACE_BODIES`, matching the prompt/result body-privacy gate). Baseline recovers none of this.

Reproduce: `python3 benchmarks/error_recovery_benchmark.py`
