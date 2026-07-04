# Channel Registry Maintainability KPI Benchmark — Issue #9

Experiment: add a config-less `echo` channel and compare the edit cost (from `channel_registry_fixtures`), then drive the real `src/channel_registry.py` to prove the candidate add works and that existing channels + the unknown->mock fallback are preserved.

- **baseline** = nested-if dispatch: a new channel needs an `(== (commchannel) X)` branch in all three dispatchers (start/receive/send).
- **candidate** = registry: one `register(Channel(...))`; dispatch code untouched.

| Metric | baseline | candidate |
| --- | --- | --- |
| **Dispatch conditionals to add a channel** | **3** | **0** |
| Dispatch sites edited (start/receive/send) | 3 | 0 |
| Non-blank lines to add a channel | 9 | 2 |
| New channel dispatches (start/receive/send round-trip) | n/a | True |
| Existing channels still resolve | n/a | 5/5 |
| Unknown channel -> mock (explicit) | (else branch) | True |

Adding a channel drops from **3 dispatch conditionals across 3 sites** to **0** (one registry object), while all five existing channels still resolve and unknown channels still fall back to mock. Live channel start/receive/send is exercised in-container (report §5).

Reproduce: `python3 benchmarks/channel_registry_benchmark.py`
