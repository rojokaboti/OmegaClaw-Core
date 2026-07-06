# FreeCiv Turn-Cycle KPI Benchmark — Issue #25

Attempts: **5** end_turn sends against `MockProxyWS` (models the proxy's `message_validator` action-required gate + the extract/normalize rule).

- **baseline** = pre-#25 client shape `{"type":"action","action_type":"end_turn"}` (top-level `action_type` — rejected by `message_validator` with `E220`, no pid 52).
- **candidate** = `{"type":"action","action":{"action_type":"end_turn"}}` (`client.end_turn_message()`) — nested under `action`, normalized to `PACKET_PLAYER_PHASE_DONE`.

| Metric | baseline | candidate |
| --- | --- | --- |
| Turns advanced (of 5) | 0 | 5 |
| Reached turn (from 1) | 1 | 6 |
| Monotonically increasing | False | True |
| Turns observed | (none) | [2, 3, 4, 5, 6] |

The candidate advances the turn on **every** attempt (1→2→3→…); the baseline stays stuck on turn 1 (0 advances), reproducing the Issue #25 symptom and proving the envelope is the cause.

Reproduce: `python3 benchmarks/freeciv/turn_cycle_benchmark.py`
