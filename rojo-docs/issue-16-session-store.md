# Change Report — Issue #16: Session persistence, transcript search & resumable snapshots

**Branch:** `feat/session-store` (off `main`, which has #1–#15 + the whole #11–#19 cluster merged)
**Issue:** #16 — "Add session persistence, transcript search, and resumable context snapshots"
**Track:** independent (first of #16/#17/#18). Not blocked by the skill cluster.

---

## 1. Why this change exists

OmegaClaw had raw logs + `history.metta` but no user-facing session database. For benchmark
analysis and real use, users need to ask "where did we leave off?", search prior decisions,
compare runs, resume interrupted work, and audit decisions without parsing raw logs.

### Design + vetting
- stdlib **`sqlite3`** with **FTS5** full-text search (transparent LIKE fallback if a build
  lacks FTS5). No new dependency.
- **Redaction before persistence** (`redaction.redact_secrets` on every stored text) — the
  searchable/exported content is redacted at rest, so it can never leak a secret (KPI).
- Two ingest paths: a **recording API** (used live + by tests/benchmark deterministically) and
  **`ingest_trace`** which backfills from the reasoning-trace JSONL every run already writes
  (`tracing` assigns a `session_id` per run → 100% of runs get an indexed session).
- **Resume** returns the latest **snapshot** (a redacted JSON state blob, independent of the raw
  prompt log) + recent messages/tool calls — enough to continue.
- Not conflated with `metta_sessions` (#8, in-process reasoning premises) — different concept.

## 2. Before → after

| | Before | After |
|---|---|---|
| Find a prior decision | grep `history.metta` / trace JSONL by hand | `sessions search "<query>"` (FTS) |
| "Where did we leave off?" | manual log reconstruction | `sessions resume <id>` (snapshot + recent context) |
| Browse / audit runs | raw logs | `sessions list` / `show` / `export` (redacted) |
| Session index | none | every run → a queryable session id + metadata (via `ingest_trace`) |
| Secret safety | logging-time only | redacted **at rest** in the store + exports |

## 3. Files changed

| File | Change |
|---|---|
| `src/session_store.py` *(new, stdlib, self-testing)* | SQLite schema (sessions/messages/tool_calls/snapshots) + FTS5 search index (LIKE fallback); recording API (`begin_session`/`record_message`/`record_tool_call`/`record_snapshot`/`end_session`); query API (`list_sessions`/`search`/`show`/`resume`/`export`); `ingest_trace`; `reset`. All text `redact_secrets`-ed before insert. |
| `scripts/omegaclaw-sessions` *(new, argparse)* | `list` / `search` / `show` / `resume` / `export` / `ingest` (`--json`). |
| `benchmarks/session_store_benchmark.py` + `_results.{md,json}` *(new)* | 1,000-session KPI gate (latency / recall@5 / resume / leaks). |
| `Autotests/test_session_store.py` *(new)* + `run_mandatory` | 6 host tests. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../src/session_store.py`. |
| `.gitignore`, `README.md` | Ignore the runtime DB; document the store + CLI + env var. |

## 4. KPI results (`benchmarks/session_store_results.md`)

1,000-session synthetic corpus (each tagged with a unique keyword; half interrupted with a
resumable snapshot) + a secret-canary session.

| Metric | baseline | candidate |
|---|---|---|
| recall@5 (target ≥ 0.90) | 0.0 | **1.00** |
| Avg search latency over 1k corpus (target < 500 ms) | — | **~0.13 ms** |
| Resume success (target ≥ 0.80) | 0.0 | **1.00** |
| Secret leaks in search/export (target 0) | 0 | **0** |

Every tagged session is found at recall@5 in sub-millisecond time, all interrupted fixtures
resume with a usable snapshot + recent context, and the secret canary never appears in search or
export output. Baseline (raw logs) offers none of this. `sys.exit(1)` on regression.

## 5. End-to-end validation

- `python3 src/session_store.py` → self-tests pass (record, search, resume, show/export,
  redaction, ingest).
- `python3 Autotests/test_session_store.py` → 6/6.
- `python3 benchmarks/session_store_benchmark.py` → `KPI GATE: PASSED`.
- CLI: `list` / `search` / `resume` exercised by hand against a temp DB.
- **Live wiring (documented):** `sessions ingest memory/traces/<date>.jsonl` backfills the store
  from any run's reasoning trace (the trace already carries the `session_id`).

### Post-review fix (PR #40 review) — two persistence-contract bugs
1. **`meta` persisted/exported unredacted.** Everything else was redacted, but `begin_session`
   stored `json.dumps(meta)` raw, so a secret in `meta` leaked via `show`/`export`. **Fix:**
   `meta` is now `redact_secrets(json.dumps(meta))`-ed at rest like every other field.
2. **Reusing a session id kept stale rows.** `INSERT OR REPLACE` updated the sessions row but
   left old `messages`/`tool_calls`/`snapshots`/search rows attached (no FK cascades), so a
   restarted id mixed unrelated old context into `show`/`resume`/`search` (correctness +
   privacy). **Fix:** `begin_session` = a fresh session — `_clear_session_rows` transactionally
   deletes all dependent rows first (FTS5 rows deleted **by rowid**, since FTS5 ignores
   `DELETE ... WHERE <unindexed col>`). Regression tests:
   `test_meta_redacted_in_show_and_export`, `test_reused_session_id_clears_stale_rows`.
3. **`ingest_trace` used the wrong phase names.** It handled invented phases
   (`llm`/`input`/`result`) but `src.tracing` actually emits `iteration_start` / `llm_call` /
   `action_parse` / `policy_decision` / `iteration_result` / `error` / `iteration_end`, so real
   traces created a session row with **no** searchable/resumable content. **Fix:** map the real
   phases, producing useful searchable summaries even **without** bodies (tool names from
   `action_parse.tools`, provider/model from `llm_call`, result size, error codes) and ingesting
   the redacted prompt/response/result bodies when `OMEGACLAW_TRACE_BODIES` was set; also capture
   the session provider. Regression test `test_ingest_real_tracing_trace` generates a trace
   **through `src.tracing`**, ingests it, and asserts `show`/`search` contain the event summaries
   (searchable by tool name + provider). Suite now 9 tests; KPI gate still passes.

## 6. Reviewer guide

```bash
git checkout feat/session-store
python3 src/session_store.py
python3 Autotests/test_session_store.py
python3 benchmarks/session_store_benchmark.py      # KPI GATE: PASSED

# Hand demo — record, search, resume (redaction included):
export OMEGACLAW_SESSION_DB=$(mktemp -d)/s.db
python3 - <<'PY'
import sys; sys.path.insert(0,"src"); import session_store as ss
ss.begin_session("d1", channel="irc", task="deploy widget")
ss.record_message("d1",1,"assistant","token sk-ant-DEADBEEFdeadbeef01234567 used")
ss.record_snapshot("d1",1,{"next":"run deploy"}); ss.end_session("d1","interrupted")
print("search:", ss.search("widget"))
print("resume:", ss.resume("d1")["latest_snapshot"])
import json; assert "sk-ant-DEADBEEF" not in json.dumps(ss.export("d1"))  # redacted at rest
print("no secret leak: OK")
PY
python3 scripts/omegaclaw-sessions list
```

## 7. Risk / rollback
- **Additive + isolated.** New module + CLI; nothing in the existing loop/skills changes. The DB
  is created on first use under `memory/` (gitignored) — no runtime artifacts committed.
- **Secret-safe at rest:** every stored field is redacted before insert; verified by the
  leak-canary test + benchmark (0 leaks in search/export).
- **Portable:** stdlib `sqlite3`; FTS5 with a LIKE fallback so it works even on a build without
  FTS5. Search is sub-millisecond at 1k sessions.
- Follow-up branch off `main`; PR against `rojokaboti/OmegaClaw-Core`. Next in the independent
  track: **#18 delegation** (benefits from this session store) and **#17 cron/webhook**.
