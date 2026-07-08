# Delegation KPI Benchmark — Issue #18

12 independent subtasks (short sleep + artifact) run serially vs. via isolated concurrent subagents (`src/delegation.py`), plus cross-workdir-write and cancellation probes.

- **baseline** = single-loop, no delegation → serial execution, no isolation/cancellation.
- **candidate** = concurrent isolated subagents with containment + cancellation contract.

| Metric | baseline | candidate |
| --- | --- | --- |
| Subtasks | 12 | 12 |
| Serial wall-clock (s) | 1.2506 | 1.2506 |
| Parallel wall-clock (s) | 1.2506 | 0.2117 |
| Wall-clock improvement % (target >= 30) | 0.0 | 83.1 |
| Success rate | 1.0 | 1.0 |
| Structured outputs (session id + artifact path) | False | True |
| Isolation violations (target 0) | 0 | 0 |
| Clean cancellation + workspace cleanup | False | True |

Candidate runs 12 subtasks **83% faster** than serial (0.21s vs 1.25s) with structured per-subagent outputs, **0** isolation violations, and clean cancellation — none of which the single-loop baseline provides.

Reproduce: `python3 benchmarks/delegation_benchmark.py`
