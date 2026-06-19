# Memory/RAG Provenance KPI Benchmark — Issue #5

Fixture dataset: **7 facts** across game_state / user / knowledge_prior / tool_result / llm, including two earlier-turn game facts explicitly superseded by the current turn.

- **baseline** = pre-change metadata (`source/breadcrumb/type/time`; no source_type/confidence).
- **candidate** = provenance schema (source, source_type, confidence, timestamp + filters).

| Metric | baseline | candidate |
| --- | --- | --- |
| Provenance coverage (full source+type+confidence+time) | 0/7 | 7/7 |
| Source-type filter correct | n/a | True |
| Min-confidence filter correct | n/a | True |
| Supersession exclusion (default) | n/a | True |
| Precision@5 proxy (drop superseded + low-confidence via the implemented filters) | 0.80 | 1.00 |

Candidate exposes provenance for every item and applies the **implemented** query-path filters (provenance scoping + supersession exclusion + min_confidence) to drop the superseded earlier-turn facts and the low-confidence LLM guess, raising precision. (Semantic precision@5 with real embeddings is validated in-container; this host benchmark proves the schema + filters deterministically using the same `matches_filters` logic as production.)

Reproduce: `python3 benchmarks/memory_provenance_benchmark.py`
