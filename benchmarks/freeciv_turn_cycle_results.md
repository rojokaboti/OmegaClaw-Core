# FreeCiv Turn-Cycle KPI Benchmark — Issue #25

Attempts: **5** end_turn sends against `MockProxyWS` (replicates the freeciv-proxy action-extraction rule at `llm_handler.py:1936`).

- **baseline** = pre-#25 client shape `{"type":"action","action_type":"end_turn"}` (top-level `action_type`, silently dropped → empty action → no pid 52).
- **candidate** = `{"type":"action","data":{"action_type":"end_turn"}}` (`client.end_turn_message()`), normalized to `PACKET_PLAYER_PHASE_DONE`.

| Metric | baseline | candidate |
| --- | --- | --- |
| Turns advanced (of 5) | 0 | 5 |
| Reached turn (from 1) | 1 | 6 |
| Monotonically increasing | False | True |
| Turns observed | (none) | [2, 3, 4, 5, 6] |

The candidate advances the turn on **every** attempt (1→2→3→…); the baseline stays stuck on turn 1 (0 advances), reproducing the Issue #25 symptom and proving the envelope is the cause.

Reproduce: `python3 benchmarks/freeciv_turn_cycle_benchmark.py`
