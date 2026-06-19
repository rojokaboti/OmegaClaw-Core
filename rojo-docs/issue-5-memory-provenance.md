# Change Report — Issue #5: Provenance-Aware Memory & RAG Metadata

**Branch:** `feat/memory-provenance` (off `main`, which has #1–#4 merged)
**Issue:** #5 — "Add provenance-aware memory and RAG metadata schema"

---

## 1. Why this change exists

Memory and retrieved facts carried no provenance: knowledge-prior chunks stored only
`{source, breadcrumb, type, time}`, and long-term memories had no schema for *source type*,
*confidence*, *timestamp*, *session/turn*, or related symbolic *atoms*. That limits
auditability and makes it impossible for evals to tell a deterministic game-state fact from
an LLM guess or a stale/superseded one.

### Accuracy correction
The issue's step 2 ("extend `lib_chromadb.remember`") targets **`petta_lib_chromadb`, an
external git-imported library** (`lib_omegaclaw.metta:24-25`) that is **not editable in this
repo**. `src/rag.py` does own a chromadb client to the shared `"memories"` collection, so the
provenance layer is built there. Existing `remember`/`query` and the external library are
**untouched** (backward-compatible); structured claims live in the same collection, so the
agent's normal `query` still recalls them by similarity, while `query-claims` adds the
provenance/filter view.

## 2. Before → after

| | Before | After |
|---|---|---|
| Knowledge-prior metadata | `source, breadcrumb, type, time` | **full schema via `build_metadata`** (claim=breadcrumb, source, source_type=knowledge_prior, confidence 0.7, created_at, atoms_json, supersedes, superseded) + legacy breadcrumb/type/time |
| Long-term memory schema | none (plain string) | `remember-claim` → `{claim, source, source_type, confidence, created_at, session_id, turn_id, atoms_json, supersedes}` |
| Confidence by source | none | game_state 1.0, tool_result 0.9, user 0.8, knowledge_prior 0.7, **llm 0.55** |
| Retrieval | similarity only | `query_claims` exposes provenance + filters by source_type / min_confidence / session / turn / time; **by default scopes to provenance-bearing records and excludes superseded ones** |
| Agent skills | `remember`, `query` | + `remember-claim` (agent belief → llm source), `query-claims` (provenance-annotated) |

## 3. Files changed

| File | Change |
|---|---|
| `src/memory_schema.py` *(new)* | Pure schema: `build_metadata`/`validate_metadata`/`DEFAULT_CONFIDENCE`/`build_where`/`matches_filters`/`claim_id`. Chroma-backed `remember_claim`/`query_claims` (reuse `rag._get_collection()`), `remember_claim_llm`/`query_claims_text` MeTTa wrappers. `atoms`→JSON string (chroma scalar-only). |
| `src/rag.py` | Knowledge-prior chunk metadata gains `source_type`/`confidence`; imports `KNOWLEDGE_PRIOR_CONFIDENCE`. No indexing behavior change. |
| `src/memory.metta` | New `(remember-claim …)` / `(query-claims …)` skills; `remember`/`query` byte-identical. |
| `src/helper.py`, `src/action_protocol.py`, `src/skills.metta` | Register the two new tools (`LLM_COMMANDS`, `ARG_SPEC`, `getSkills`). |
| `lib_omegaclaw.metta` | Register `./src/memory_schema.py` as a Python module. |
| `Autotests/test_memory_schema.py` *(new)* | Pure tests (always run) + chroma-backed tests (in-memory `EphemeralClient`, `importorskip` → run in-container). In `run_mandatory` + CI self-test. |
| `benchmarks/memory_provenance_*` *(new)* | Fixture dataset + deterministic provenance/filter/precision benchmark + committed results. |

## 4. KPI results (`benchmarks/memory_provenance_results.md`)

7-fact fixture (game_state, user, knowledge_prior, tool_result, llm, plus two earlier-turn game facts explicitly **superseded** by the current turn):

| Metric | baseline | candidate |
|---|---|---|
| Provenance coverage (source+type+confidence+time) | 0/7 | **7/7** |
| Source-type filter correct | n/a | **yes** |
| Min-confidence filter correct | n/a | **yes** |
| Supersession exclusion (default) | n/a | **yes** |
| Precision@5 proxy | 0.80 | **1.00** |

Candidate exposes provenance for every item and applies the **implemented** query-path filters
(`matches_filters`: provenance scoping + supersession exclusion + min_confidence) to drop the
low-confidence LLM guess and the superseded earlier-turn facts, raising precision to 1.00. The
benchmark uses the same `matches_filters` logic the production query path uses (no fixture-only
ranking). Semantic precision@5 with real embeddings is validated in-container.

## 5. End-to-end validation (in-container)

- **Chroma-backed `test_memory_schema.py` in the container: 12/12 pass** (the chroma tests
  execute there — chromadb present; they skip on the host runner). Note: tests use a unique
  in-memory collection each, since chromadb `EphemeralClient` shares in-process state.
- **`remember_claim` → `query_claims` round-trip (real chromadb client):** writing a
  `game_state` claim (conf 1.0) and an `llm` claim (conf 0.55), then `query_claims(..., {min_confidence: 0.8})`
  returns only the game_state claim with full provenance:
  `[('City A has low foo', 'game_state', 1.0)]` — the low-confidence guess is filtered out.
- **`@run_mandatory`: 141 passed, 4 skipped, 0 failed** — was 133; +8 (the 4 skips are the
  chroma-backed tests skipping on the pytest host runner). Adding `remember-claim`/`query-claims`
  did not regress the mock suite.

## 6. Reviewer guide — test & compare against the previous version

Prereqs: Python 3.12, `pytest`. Docker in the `docker` group for §D (else prefix with `sg docker -c "…"`).

### A. Read the core diff
```bash
git checkout feat/memory-provenance
git diff main -- src/memory_schema.py src/rag.py src/memory.metta src/action_protocol.py
```

### B. Pure-Python checks (seconds, no Docker)
```bash
python3 src/memory_schema.py                  # schema self-tests
python3 Autotests/test_memory_schema.py       # pure pass; chroma-backed SKIP without chromadb
python3 benchmarks/memory_provenance_benchmark.py   # coverage/filter/precision matrix; non-zero exit on regression
```

### C. Hand demo — schema + filters (seconds)
```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "src")
import memory_schema as ms
print("game_state conf:", ms.build_metadata("c","freeciv.t42","game_state")["confidence"])  # 1.0
print("llm conf:", ms.build_metadata("c","model","llm")["confidence"])                        # 0.55
print("where(min_conf 0.8):", ms.build_where({"min_confidence":0.8}))
print("llm passes 0.8 filter?:", ms.matches_filters(ms.build_metadata("c","m","llm"), {"min_confidence":0.8}))  # False
PY
```

### D. In-container (chromadb present) — round-trip
```bash
docker build -t omegaclaw:local .
( cd Autotests && python3 -m pytest -q test_memory_schema.py )    # all 12 run (none skipped)
# remember_claim -> query_claims round-trip via the real client:
docker run --rm -i --entrypoint python3 omegaclaw:local - <<'PY'
import sys; sys.path.insert(0,"/PeTTa/repos/OmegaClaw-Core/src")
import memory_schema as ms
ms.remember_claim("City A low food", [0.1]*8, "game_state", source="freeciv.t42", turn_id=42)
ms.remember_claim("maybe plenty of food", [0.1]*8, "llm", source="model")
hi = ms.query_claims([0.1]*8, n=5, filters={"min_confidence":0.8})
print([ (r["document"], r["metadata"]["source_type"], r["metadata"]["confidence"]) for r in hi ])
PY
```
Expected: the `llm` (0.55) claim is filtered out; the `game_state` (1.0) claim is returned with full provenance.

### E. Compare to `main`
```bash
git show main:src/rag.py | sed -n '267,275p'   # old metadata: no source_type/confidence
git ls-tree main -- src/memory_schema.py        # absent on main
git diff main --stat
```

## 7. Risk / rollback
- Backward-compat: `remember`/`query` and the external `lib_chromadb` untouched; new skills are
  additive; mock memory tests assert on vector count / recalled keyword (not metadata).
- Adding tools to `LLM_COMMANDS` requires matching `ARG_SPEC` entries (done) or
  `output_format_block` KeyErrors — covered by the action-protocol tests.
- chroma metadata is scalar-only → `atoms` stored as a JSON string.
- **PR #24 review fixes:** (1) `rag.py` imports `memory_schema` via the repo's `try/except`
  fallback (robust under `import src.rag`, not reliant on a side-effect); (2) RAG chunks now
  carry the full schema via `build_metadata`; (3) **supersession is implemented in the query
  path** — `remember_claim(supersedes=old)` marks the old record `superseded=True`, and
  `query_claims`/`build_where` exclude superseded records and scope to provenance-bearing ones
  by default (so legacy memories / hash sentinels aren't surfaced). Docs/benchmark describe only
  the wired filters (no "stale"/recency claims; the benchmark uses the same `matches_filters`).
- **Deferred:** there is no FreeCiv/game producer in this repo, so `game_state` is supported as
  a source_type/confidence (1.0) for future producers to populate; nothing to wire today.
