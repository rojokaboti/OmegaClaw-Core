# Error-Recovery KPI Benchmark — Issue #10

Fixture dataset: **5 fixtures**, one per canonical error category (`error_recovery_fixtures.FIXTURES`), driven through the real `src/errors.py` + `src/action_protocol.py`.

- **baseline** = original `asi-alliance` string-only feedback (`…NOTHING_WAS_DONE…`): no category, failed action, retryability, repair hint, or trace id — one unknown bucket.
- **candidate** = structured error events: five machine-readable categories, each a full event emitted to the reasoning trace under the iteration's `trace_id`.

| Metric | baseline | candidate |
| --- | --- | --- |
| Machine-readable error_type (one of 5) | 0.00 | 1.00 |
| Correct category | 0.00 | 1.00 |
| Failed action captured | 0.00 | 1.00 |
| Retryable flag present | 0.00 | 1.00 |
| Concise repair hint | 0.00 | 1.00 |
| Trace id present | 0.00 | 1.00 |
| Next-turn recovery (corrected input parses) | 0.00 | 1.00 |
| Unknown / unclassified bucket | 1.00 | 0.00 |

Structured error events emitted to the trace: **5** (counts by type: {'parse_error': 1, 'unknown_tool': 1, 'schema_validation_error': 1, 'tool_policy_denied': 1, 'tool_runtime_error': 1}).

The candidate classifies **every** fixture into one of the five categories (unknown bucket 0.00 vs the baseline's 1.00), attaches a concise repair hint suitable for feeding back to the model, and every corrected next-turn input parses cleanly.

Reproduce: `python3 benchmarks/error_recovery_benchmark.py`
