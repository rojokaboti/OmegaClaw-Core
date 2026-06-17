# Action Protocol KPI Benchmark — Issue #1

Corpus: **54 synthetic LLM outputs** across valid_json, legacy_text, malformed_json, unknown_tool, multiline_send, file_ops, metta_expr.

- **Baseline** = `helper.balance_parentheses` (original repo behavior)
- **Candidate (json)** = strict JSON mode, the shipping default. Legacy text is deliberately rejected (model is re-prompted for JSON), which is why some legacy-text fixtures count as parse failures here.
- **Candidate (auto)** = JSON with legacy fallback — the migration path.

| Metric | Baseline | Candidate (json) | Candidate (auto) |
| --- | --- | --- | --- |
| Overall parse success rate | 29.6% | 72.2% | 83.3% |
| Execute success rate | 39.5% | 60.5% | 100.0% |
| Reject (validation) success rate | 6.2% | 100.0% | 43.8% |
| Parse failures (count) | 38 | 15 | 9 |
| **False accepts (unknown tool → eval)** | 38 | 0 | 9 |
| False rejects (lost legit action) | 23 | 15 | 0 |
| NOTHING_WAS_DONE outcomes | 1 | 31 | 7 |

- **json: parse-failure reduction 60.5%** (38 → 15).
- **auto: parse-failure reduction 76.3%** (38 → 9).
- **Unsafe unknown-tool accepts: baseline 38 → json 0, auto 9.**

Reproduce: `python3 benchmarks/run_benchmark.py`
