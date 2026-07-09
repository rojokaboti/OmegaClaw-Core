# Change Report — Issue #17: Cron, webhook & event-triggered autonomous runs

**Branch:** `feat/scheduler` (off `main`, which has #1–#18 merged incl. #16 sessions + #18 delegation)
**Issue:** #17 — "Add cron, webhook, and event-triggered autonomous runs"
**Track:** independent (last of #16/#17/#18). **Final issue of the batch.**

---

## 1. Why this change exists

OmegaClaw was interactive/benchmark-loop oriented; automation (monitors, daily summaries,
recurring benchmarks, CI watchers, external event handlers) required ad hoc shell wrappers. This
adds first-class **durable jobs + a scheduler + a webhook adapter**.

### Design + vetting
- **Durable** SQLite jobs store, so due jobs **survive a restart**: the scheduler holds **no
  in-memory state** between `run_due()` calls — every decision reads the persisted `next_run`,
  which is advanced **before** the job body runs, so a crash mid-run can neither lose nor
  double-fire an occurrence.
- **Injected clock** (`run_due(now=…)`) → the whole timeline is testable deterministically (no
  real waiting); fire-time drift is bounded by the heartbeat interval.
- Schedules: `once` / `interval` / a minimal 5-field `cron`.
- Each fire runs in its **own session** (#16) with delivery/alert hooks; `on_empty` distinguishes
  silent watchdogs from alerting ones; jobs **chain** on the previous output.
- **Safeguards:** unsafe/duplicate job ids rejected (`is_safe_skill_name` — reusing the shared
  containment helper), a per-job **minimum interval** (runaway guard), and **recursion refused**
  while a job runs (no self-scheduling storms) — pre-empting the id/loop issues flagged in
  earlier reviews.
- **Webhooks:** subscriptions carry an HMAC secret; `hmac.compare_digest` validates the
  signature — a bad/missing signature is **rejected** with no run.
- Execution is worker-pluggable: `run_due(runner=…)`. Default echoes the prompt; a live
  deployment passes a runner that drives an agent run (documented). All KPIs are proven by the
  deterministic default + injected clock.

## 2. Before → after

| | Before | After |
|---|---|---|
| Scheduled runs | ad hoc shell/cron wrappers | durable `once`/`interval`/`cron` jobs |
| Restart | due jobs lost / re-run ad hoc | persisted `next_run` → fire exactly once |
| Event triggers | none | HMAC-validated webhook → agent run |
| Failure / empty output | silent | alerts on failure; `on_empty` silent vs alert |
| Chaining / safety | none | context chaining; recursion + runaway guards |

## 3. Files changed

| File | Change |
|---|---|
| `src/scheduler.py` *(new, stdlib, self-testing)* | Durable jobs + webhooks SQLite store; `create_job`/`list`/`get`/`pause`/`resume`/`remove`; `_cron_next` (minimal 5-field cron) + `once`/`interval`; `due_jobs`/`run_due` (injected clock, advance-before-run durability, per-job session, delivery/alert, `on_empty`, chaining); `run_now`; `webhook_subscribe`/`list`/`remove`/`event` (HMAC). Recursion + runaway + unsafe/duplicate-id guards. |
| `scripts/omegaclaw-cron` *(new, argparse)* | `create/list/show/pause/resume/remove/run/tick` + `webhook subscribe/list/remove/event` (`--json`). |
| `benchmarks/scheduler_benchmark.py` + `_results.{md,json}` *(new)* | 20 one-shot + 5 recurring + 10 webhook events, restart + 2 failures, KPI gate. |
| `Autotests/test_scheduler.py` *(new)* + `run_mandatory` | 9 host tests. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../src/scheduler.py`. |
| `.gitignore`, `README.md` | Ignore the runtime jobs DB; document the scheduler + CLI. |

## 4. KPI results (`benchmarks/scheduler_results.md`)

20 one-shot + 5 recurring jobs over a simulated 1s-heartbeat timeline with a mid-run restart +
2 induced failures, and 10 webhook events (alternating valid/invalid signatures).

| Metric | baseline | candidate |
|---|---|---|
| One-shot jobs fired exactly once | 0 | **20/20** |
| Jobs lost or duplicated across restart (target 0) | 20 | **0** |
| Recurring jobs fired | False | **True** |
| Failure alerts delivered (target 2) | 0 | **2** |
| Max fire-time drift (target < 2 s) | — | **0.0 s** |
| Webhook valid ran / invalid rejected (of 5 each) | 0 / 0 | **5 / 5** |

Every one-shot job fires exactly once with zero lost/duplicated across a restart, recurring jobs
fire, failures alert, drift is within tolerance, and all invalid webhook signatures are rejected
while valid ones run. `sys.exit(1)` on regression. Satisfies the issue's gate (no due jobs lost
after restart, drift within threshold).

## 5. End-to-end validation

- `python3 src/scheduler.py` → self-tests pass (once/interval/cron, unsafe/runaway rejection,
  restart safety, failure alerts, on_empty, recursion guard, webhook validation).
- `python3 Autotests/test_scheduler.py` → 9/9.
- `python3 benchmarks/scheduler_benchmark.py` → `KPI GATE: PASSED`.
- CLI exercised by hand: create cron/interval jobs, `run`, `tick`, `webhook subscribe` + a valid
  event (runs) + an invalid signature (`REJECTED`, exit 1).

### Post-review fix (PR #42 review) — two correctness bugs
1. **Cron matching ignored minute/hour/dom/mon for many specs.** An operator-precedence bug left
   the second DOW term OUTSIDE the conjunction (`… and dow_a or dow_b`), so a `*` DOW made daily/
   monthly specs match **every minute** (`0 0 * * *` fired at 12:34) — a real autonomous-runaway/
   cost risk. **Fix:** DOW is computed as a single value (with `0`/`7` = Sunday normalization) and
   kept **inside** the five-field conjunction. Verified `0 0 * * *` @12:34 → False, next fire next
   midnight; `*/5` → next boundary; DOW specs no longer bypass minute/hour.
2. **Webhook transient job id could collide with + delete a durable job.** The id used a small
   predictable `time%1e6` space, `create_job`'s result was unchecked, and `remove(jid)` ran
   unconditionally — so a valid webhook could delete an unrelated durable job on collision.
   **Fix:** collision-resistant `uuid4` suffix, abort if creation failed, and only `remove` the id
   we actually created (in a `finally`). Verified a forced collision leaves the durable job intact.
Regression tests: `test_cron_respects_all_five_fields`,
`test_webhook_transient_id_never_deletes_durable_job`. Suite now 11 tests; KPI gate still passes.

### Post-review fix round 2 (PR #42 re-review) — three correctness bugs
1. **Webhook runner never received the event.** The handler only saw the generic ctx; the parsed
   event body was absent. **Fix:** the validated event is injected as `ctx["event"]` (via a new
   single-job executor's `extra_ctx`), so handlers can act on issue titles / branch names / etc.
2. **`*/0` (and out-of-range/non-int) cron crashed `create_job`** (`ZeroDivisionError`). **Fix:**
   a guard in `_cron_field_match` (step > 0) + an up-front `_validate_cron` so a malformed spec
   returns a structured `ok: false` (fail-closed) instead of crashing or silently never firing.
3. **`run_now` silently resumed a paused job and rescheduled it** (and could fire *other* due
   jobs). **Fix:** extracted `_execute_one` — `run_now` executes ONLY the named job with
   `advance=False`, so it never mutates durable `enabled`/`next_run` or touches sibling jobs (the
   webhook path uses the same executor, fixing that latent bug too).
Regression tests: `test_webhook_runner_receives_event_payload`, `test_invalid_cron_fails_closed`,
`test_run_now_does_not_resume_paused_or_reschedule`. Suite now 14 tests; KPI gate still passes.

### Post-review fix round 3 (PR #42 re-review) — concurrent double-run race
`run_due` did a **select-then-update**: two heartbeats could both `due_jobs()`-select the same
row before either advanced it, so the job ran **twice** (sharing session id `cron-<job>-1`, with
`fires` ending at 1 — hiding the dup). **Fix:** an **atomic claim** — `_execute_one` advances via
`UPDATE jobs SET next_run=?, last_run=?, fires=fires+1 WHERE id=? AND enabled=1 AND next_run=?`
(compare-and-swap on the selected `next_run`) and runs the body **only if `rowcount == 1`**; the
loser returns `None` and skips. The session id is derived from the **persisted, atomically
incremented** `fires`, so concurrent attempts can't share one. `connect()` also sets
`busy_timeout` so concurrent writers wait rather than error. Regression tests:
`test_concurrent_heartbeats_run_due_occurrence_once` (deterministic stale-select) and
`test_concurrent_run_due_threads_fire_once` (two racing threads). Suite now 16 tests; KPI gate
still passes.

## 6. Reviewer guide

```bash
git checkout feat/scheduler
python3 src/scheduler.py
python3 Autotests/test_scheduler.py
python3 benchmarks/scheduler_benchmark.py          # KPI GATE: PASSED

# Hand demo — durable schedule + HMAC webhook (deterministic injected clock):
python3 - <<'PY'
import sys, hmac, hashlib, json; sys.path.insert(0, "src")
import scheduler as sch
sch.create_job("j", "once", "1000010", prompt="hi", now=1000000)
print("due@+5:", sch.run_due(now=1000005, runner=lambda j,c: "out")["count"])   # 0
print("due@+11:", sch.run_due(now=1000011, runner=lambda j,c: "out")["count"])  # 1
sch.webhook_subscribe("gh", "secret", {"prompt": "x"})
raw = json.dumps({"e": "push"}, sort_keys=True).encode()
sig = hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
print("valid:", sch.webhook_event("gh", {"e": "push"}, sig, runner=lambda j,c: "ok")["ok"])
print("invalid rejected:", sch.webhook_event("gh", {"e": "push"}, "bad").get("rejected"))
PY
```

## 7. Risk / rollback
- **Additive + isolated.** New module + CLI; nothing in the existing loop changes. The jobs DB is
  created on first use under `memory/` (gitignored) — no runtime artifacts committed.
- **Restart-safe by construction:** no in-memory scheduler state; `next_run` persisted and
  advanced before running.
- **Safe against runaway/abuse:** unsafe/duplicate ids rejected, minimum interval, recursion
  guard, HMAC-validated webhooks; all text redacted before persistence.
- **Deterministic:** injected clock → no flaky timing in tests/benchmark; the live heartbeat is a
  loop calling `run_due()` (documented).
- Follow-up branch off `main`; PR against `rojokaboti/OmegaClaw-Core`. **This is the last issue —
  it completes the entire OpenClaw/Hermes parity batch (#11–#19 + #16/#17/#18).**
