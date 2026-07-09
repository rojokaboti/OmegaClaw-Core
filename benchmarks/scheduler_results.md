# Scheduler KPI Benchmark — Issue #17

20 one-shot + 5 recurring jobs over a simulated timeline (injected clock) with a mid-run restart + 2 induced failures, plus 10 webhook events (alternating valid/invalid signatures), through the real `src/scheduler.py`.

- **baseline** = no scheduler/webhook: ad hoc shell wrappers, no restart recovery or signature validation.
- **candidate** = durable jobs + run_due + HMAC webhooks.

| Metric | baseline | candidate |
| --- | --- | --- |
| One-shot jobs fired exactly once (target 20/20) | 0 | 20 |
| Jobs lost or duplicated across restart (target 0) | 20 | 0 |
| Recurring jobs fired | False | True |
| Failure alerts delivered (target 2) | 0 | 2 |
| Max fire-time drift s (target < 2.0) | None | 0.0 |
| Webhook valid events ran (of 5) | 0 | 5 |
| Webhook invalid signatures rejected (of 5) | 0 | 5 |

Candidate fires **20/20** one-shot jobs exactly once with **0** lost/duplicated across a restart, delivers **2** failure alerts, keeps drift at **0.0s**, and rejects **5/5** invalid webhook signatures while running **5/5** valid ones — the baseline has no first-class equivalent.

Reproduce: `python3 benchmarks/scheduler_benchmark.py`
