# Memory/RAG Provenance KPI Benchmark — Issue #5

Fixture dataset: **7 facts** across game_state / user / knowledge_prior / tool_result / llm, including a stale (earlier-turn) fact and a superseded fact.

- **baseline** = pre-change metadata (`source/breadcrumb/type/time`; no source_type/confidence).
- **candidate** = provenance schema (source, source_type, confidence, timestamp + filters).

| Metric | baseline | candidate |
| --- | --- | --- |
| Provenance coverage (full source+type+confidence+time) | 0/7 | 7/7 |
| Source-type filter correct | n/a | True |
| Min-confidence filter correct | n/a | True |
| Precision@5 proxy (filter out stale/superseded/low-confidence) | 0.80 | 1.00 |

Candidate exposes provenance for every item and filters by source type and confidence, raising precision by excluding stale/superseded/low-confidence facts. (Semantic precision@5 with real embeddings is validated in-container; this host benchmark proves the schema + filters deterministically.)

Reproduce: `python3 benchmarks/memory_provenance_benchmark.py`
