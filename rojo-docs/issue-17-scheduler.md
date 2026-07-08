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
