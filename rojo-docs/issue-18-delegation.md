# Change Report — Issue #18: Multi-agent delegation & isolated subagent workspaces

**Branch:** `feat/delegation` (off `main`, which has #1–#16 merged incl. the full #11–#19 cluster + #16 sessions)
**Issue:** #18 — "Add multi-agent delegation and isolated subagent workspaces"
**Track:** independent. Builds on the #16 session store.

---

## 1. Why this change exists

Complex tasks benefit from parallel independent investigations, review agents, and sandboxed
workers. OmegaClaw was a single loop. This adds an explicit, bounded, auditable **delegation
primitive**: run a batch of subtasks concurrently, each isolated, with structured results.

### Design + vetting
- A subtask is `{"id", "run": callable(ctx) -> summary, "timeout"?}`. The worker gets a
  `WorkerContext` with its **private workdir**, `cancelled()`, and `write_artifact(relpath,
  content)` — the **only** channel back to the parent, which **rejects any path escaping the
  workdir** (`..`/absolute/symlink). This makes "subagents cannot modify parent state except
  through declared artifacts" a structural guarantee, not a convention.
- **Concurrency** via `concurrent.futures` threads (limit configurable); **per-worker timeout**;
  cooperative **cancellation** via a shared `Event` (parent interruption → workers observe
  `ctx.cancelled()`, not-yet-started workers skipped, batch returns promptly).
- **Isolated session per subagent** in the #16 store (`deleg-<parent>-<id>`) → auditable via
  `sessions show/search`. NB: each worker thread opens its **own** sqlite connection (sqlite3
  forbids cross-thread connection sharing) — a bug caught in self-test and fixed.
- **No nested delegation by default** (a `threading.local` guard) → no runaway recursion.
- Deterministic + host-testable (short sleeps), so the KPI speedup + isolation are proven
  without a live LLM. Worker-agnostic: a live subagent LLM loop is just another `run` callable
  (documented as the live wiring; not required for the primitive or its KPIs).

## 2. Before → after

| | Before | After |
|---|---|---|
| Parallel subtasks | single loop, serial | concurrent isolated subagents (bounded) |
| Subagent workspace | shared | private workdir + session per subagent |
| Parent mutation channel | ad hoc | declared artifacts only (containment-enforced) |
| Cancellation | none | parent `Event` cancels children; clean cleanup |
| Recursion safety | none | nested delegation refused by default |
| Result | ad hoc | structured (status / session id / artifacts / duration) |

## 3. Files changed

| File | Change |
|---|---|
| `src/delegation.py` *(new, stdlib, self-testing)* | `delegate(subtasks, concurrency, timeout, cancel_event, allow_nested)` (thread pool, per-worker timeout, cancellation, structured batch result); `WorkerContext` (private workdir + `write_artifact` containment); per-subagent session recording (#16, own connection per thread); no-nested guard; `cleanup`. |
| `benchmarks/delegation_{benchmark}.py` + `_results.{md,json}` *(new)* | 12-subtask serial-vs-parallel KPI + cross-workdir-write + cancellation probes. |
| `Autotests/test_delegation.py` *(new)* + `run_mandatory` | 8 host tests. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../src/delegation.py`. |
| `README.md` | Documents the delegation primitive. |

## 4. KPI results (`benchmarks/delegation_results.md`)

12 independent subtasks (sleep + artifact), serial vs. concurrent, + isolation & cancellation probes.

| Metric | baseline | candidate |
|---|---|---|
| Wall-clock improvement % (target ≥ 30) | 0.0 | **~88%** |
| Success rate | 1.0 | **1.00** |
| Structured outputs (session id + artifact path) | False | **True** |
| Isolation violations (target 0) | 0 | **0** |
| Clean cancellation + workspace cleanup | False | **True** |

Concurrent delegation runs the batch ~88% faster than serial with structured per-subagent
outputs, zero isolation violations (cross-workdir writes blocked), and clean cancellation.
`sys.exit(1)` on regression (gate: ≥30% speedup AND zero isolation violations).

## 5. End-to-end validation

- `python3 src/delegation.py` → self-tests pass (parallel speedup, containment, timeout,
  cancellation, no-nested).
- `python3 Autotests/test_delegation.py` → 8/8 (incl. concurrency-limit serialization, worker
  error isolation, subagent recorded in the session store).
- `python3 benchmarks/delegation_benchmark.py` → `KPI GATE: PASSED`.

### Post-review fix (PR #41 review) — three isolation/timeout contract bugs
1. **Unsafe task id escaped `deleg_root`.** The id was used directly in the workdir path, so
   `id="../escape"` put the workspace outside the root and survived `cleanup`. **Fix:** validate
   ids up front with `skill_loader.is_safe_skill_name` (reject `..`/separators/absolute/empty) —
   the batch is refused before anything runs — plus a realpath-containment check on the workdir.
2. **Timed-out workers kept running and wrote artifacts post-timeout**, and `delegate` blocked
   on the runaway (the `with ThreadPoolExecutor` did a waiting shutdown). **Fix:** each task has
   its own cancel `Event`; on `FutureTimeout` it's set and `write_artifact` **refuses** once
   cancelled/timed-out, and the executor is shut down with `wait=False, cancel_futures=True` so
   `delegate` **returns promptly** (cooperative-timeout contract; a late write is refused).
3. **Duplicate task ids collided** workspace/session/results (second overwrote first). **Fix:**
   duplicate ids are rejected in the same up-front validation.
Regression tests: `test_unsafe_task_id_rejected_before_running`, `test_duplicate_task_ids_rejected`,
`test_timeout_refuses_post_timeout_writes_and_returns_promptly`. Suite now 11 tests; KPI gate
still passes.

## 6. Reviewer guide

```bash
git checkout feat/delegation
python3 src/delegation.py
python3 Autotests/test_delegation.py
python3 benchmarks/delegation_benchmark.py         # KPI GATE: PASSED

# Hand demo — parallel subagents, isolation, structured results:
python3 - <<'PY'
import sys, time; sys.path.insert(0, "src")
import delegation as dg
def work(ctx):
    time.sleep(0.1); ctx.write_artifact("out.txt", "done " + ctx.task_id); return "ok " + ctx.task_id
b = dg.delegate([{"id": "t%d" % i, "run": work} for i in range(6)], parent_id="demo", concurrency=6)
print("wall_clock:", b["wall_clock"], "counts:", b["counts"])
print("first result:", {k: b["results"][0][k] for k in ("id","session_id","status","artifacts")})
dg.cleanup(b)
PY
```

## 7. Risk / rollback
- **Additive + isolated.** New module; nothing in the existing loop changes. Workspaces are temp
  dirs cleaned by `cleanup()`; no runtime artifacts committed.
- **Bounded + safe:** concurrency limit, per-worker timeout, cancellation, no-nested guard, and
  artifact containment (the only parent-mutation channel). Worker failures are isolated (one bad
  subagent never crashes the batch).
- **Thread-correct session recording:** each worker opens its own sqlite connection (cross-thread
  sharing is forbidden) — verified by the session-recording test.
- Follow-up branch off `main`; PR against `rojokaboti/OmegaClaw-Core`. Remaining independent
  issue: **#17 cron/webhook/event triggers**.
