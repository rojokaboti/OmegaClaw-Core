# LLM Raw-Logging Privacy Benchmark — Issue #3

One secret-bearing model response logged under each config (GitHub token + PAT, OpenAI/Anthropic keys, bearer token, AWS key, long base64 secret).

- **baseline** = pre-fix `_log_raw` (`raw={raw!r}`, unconditional)
- **default** = new metadata-only default (no env)
- **debug** = new `OMEGACLAW_DEBUG_LLM_RAW=1` (raw, redacted)

| Metric | baseline | default | debug |
| --- | --- | --- | --- |
| Raw body in log | True | False | True |
| **Unredacted secret leaks** | 7 | 0 | 0 |
| Metadata present | True | True | True |

Baseline leaked 7/7 secret-shaped strings; the new default leaks 0 (and logs no raw body) while keeping metadata; debug shows raw context with 0 unredacted secrets.

Reproduce: `python3 benchmarks/llm_logging_benchmark.py`
