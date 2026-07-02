# Captured freeciv-llm samples (byte-real, live)

Captured live from a running `taso-ventures/freeciv-llm` stack during Issue #6 validation.

- `real_state_turn0.json` — pregame (`T000`) `llm_optimized` state. Documents the **real runtime
  shape** from `civcom.build_llm_optimized_state`, which differs from the documented
  `state_extractor._format_llm_optimized_state`: `strategic.score` (not
  `victory_progress.current_score`), `economic.gold`/`research` (not `economic.resources.*`),
  `tactical.active_units`/`visible_threats`. The adapter handles **both** shapes.
- `real_state_turn1.json` — a **started** game (turn 1) with the player's 7 starting units
  (`startunits "cccwwwx"` = 3 settlers, 3 workers, 1 caravan). Anchors the populated-state
  regression test.

**Game-start note:** freeciv-llm civservers default to `minplayers=2`, so a single agent + aifill
stays in pregame forever. To start: while in pregame send `/set minplayers 1` then `/start`
(server commands over the `chat` message type). This is baked into `../live_play.py`.
