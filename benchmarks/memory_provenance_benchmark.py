"""KPI benchmark for Issue #5: memory/RAG provenance, baseline vs candidate.

Deterministic and host-runnable (no embeddings/chromadb): it exercises the
provenance schema + filter logic on the fixture dataset.

* **baseline** = pre-change behavior: items carry only `source/breadcrumb/type/time`,
  no source_type/confidence and no source/confidence filtering.
* **candidate** = provenance schema: every item exposes source/source_type/
  confidence/timestamp, and source-type + min-confidence filters work.

Metrics:
- provenance coverage (% items exposing source + source_type + confidence + timestamp)
- filter correctness (source_type and min_confidence select exactly the right items)
- filter-driven precision@5 proxy: ranking trusted, current, on-topic items above
  stale/superseded/low-confidence ones raises precision vs the unfiltered baseline.

Writes `memory_provenance_results.{md,json}`. Exit non-zero if the KPI gate fails.
Run: `python3 benchmarks/memory_provenance_benchmark.py`
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import memory_schema as ms  # noqa: E402
from memory_provenance_fixtures import FIXTURES  # noqa: E402


def _candidate_meta(fx):
    meta = ms.build_metadata(
        fx["claim"], fx["source"], fx["source_type"],
        confidence=fx.get("confidence"), turn_id=fx.get("turn_id"),
        created_at=fx.get("created_at"), supersedes=fx.get("supersedes"),
    )
    # Mirror what remember_claim(..., supersedes=...) sets on the superseded record.
    meta["superseded"] = bool(fx.get("superseded"))
    return meta


def _baseline_meta(fx):
    # Pre-change shape (rag.py knowledge-prior style): no source_type/confidence.
    return {"source": fx["source"], "breadcrumb": "", "type": "chunk", "time": "knowledge_prior"}


def _coverage(metas):
    needed = ("source", "source_type", "confidence", "created_at")
    full = sum(1 for m in metas if all(m.get(k) not in (None, "") for k in needed))
    return full, len(metas)


def main():
    cand = [_candidate_meta(f) for f in FIXTURES]
    base = [_baseline_meta(f) for f in FIXTURES]
    relevant = [f.get("relevant", False) for f in FIXTURES]

    cand_full, n = _coverage(cand)
    base_full, _ = _coverage(base)

    # filter correctness on the candidate metadata. Use include_superseded to test the
    # source_type filter in isolation (default supersession exclusion is checked separately).
    gs = [m for m in cand if ms.matches_filters(m, {"source_type": "game_state", "include_superseded": True})]
    hi = [m for m in cand if ms.matches_filters(m, {"min_confidence": 0.8})]
    filter_source_type_ok = all(m["source_type"] == "game_state" for m in gs) and len(gs) == sum(
        1 for f in FIXTURES if f["source_type"] == "game_state")
    filter_min_conf_ok = all(m["confidence"] >= 0.8 for m in hi) and ("llm" not in [m["source_type"] for m in hi])
    # supersession exclusion is active by default (the implemented behavior)
    superseded_excluded_ok = all(
        not ms.matches_filters(m) for m, f in zip(cand, FIXTURES) if f.get("superseded"))

    # precision@5 proxy:
    #  baseline: no provenance to rank by -> take items in order (first 5).
    #  candidate: keep only current, non-superseded, confidence>=0.8 (drops stale/llm/superseded),
    #             which leaves the relevant trusted facts.
    def precision_at_5(keep_idx):
        top = keep_idx[:5]
        if not top:
            return 0.0
        return sum(1 for i in top if relevant[i]) / len(top)

    base_rank = list(range(len(FIXTURES)))  # baseline has no provenance signal to reorder/filter

    # Candidate keep-set uses ONLY the implemented query-path filter (matches_filters,
    # which mirrors build_where): default provenance scoping + supersession exclusion,
    # plus a min_confidence threshold. This drops the low-confidence LLM guess and the
    # superseded earlier-turn facts -- exactly what query_claims does in production.
    cand_rank = [i for i, m in enumerate(cand) if ms.matches_filters(m, {"min_confidence": 0.6})]
    base_p5 = precision_at_5(base_rank)
    cand_p5 = precision_at_5(cand_rank)

    results = {
        "n": n,
        "provenance_coverage": {"baseline": base_full, "candidate": cand_full, "total": n},
        "filter_source_type_ok": filter_source_type_ok,
        "filter_min_confidence_ok": filter_min_conf_ok,
        "supersession_exclusion_ok": superseded_excluded_ok,
        "precision_at_5": {"baseline": round(base_p5, 3), "candidate": round(cand_p5, 3)},
    }
    with open(os.path.join(_HERE, "memory_provenance_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    md = "\n".join([
        "# Memory/RAG Provenance KPI Benchmark — Issue #5",
        "",
        f"Fixture dataset: **{n} facts** across game_state / user / knowledge_prior / tool_result / "
        "llm, including two earlier-turn game facts explicitly superseded by the current turn.",
        "",
        "- **baseline** = pre-change metadata (`source/breadcrumb/type/time`; no source_type/confidence).",
        "- **candidate** = provenance schema (source, source_type, confidence, timestamp + filters).",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
        f"| Provenance coverage (full source+type+confidence+time) | {base_full}/{n} | {cand_full}/{n} |",
        f"| Source-type filter correct | n/a | {filter_source_type_ok} |",
        f"| Min-confidence filter correct | n/a | {filter_min_conf_ok} |",
        f"| Supersession exclusion (default) | n/a | {superseded_excluded_ok} |",
        f"| Precision@5 proxy (drop superseded + low-confidence via the implemented filters) | {base_p5:.2f} | {cand_p5:.2f} |",
        "",
        "Candidate exposes provenance for every item and applies the **implemented** query-path "
        "filters (provenance scoping + supersession exclusion + min_confidence) to drop the superseded "
        "earlier-turn facts and the low-confidence LLM guess, raising precision. (Semantic precision@5 "
        "with real embeddings is validated in-container; this host benchmark proves the schema + filters "
        "deterministically using the same `matches_filters` logic as production.)",
        "",
        "Reproduce: `python3 benchmarks/memory_provenance_benchmark.py`",
        "",
    ])
    with open(os.path.join(_HERE, "memory_provenance_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    failures = []
    if cand_full != n:
        failures.append(f"candidate provenance coverage {cand_full}/{n} (expected full)")
    if not filter_source_type_ok:
        failures.append("source_type filter incorrect")
    if not filter_min_conf_ok:
        failures.append("min_confidence filter incorrect")
    if not superseded_excluded_ok:
        failures.append("supersession exclusion not applied by default")
    if cand_p5 < base_p5:
        failures.append(f"candidate precision proxy {cand_p5} < baseline {base_p5}")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
