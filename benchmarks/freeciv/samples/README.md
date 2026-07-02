# Captured freeciv-llm samples

`real_state_turn0.json` — a **byte-real** `llm_optimized` state captured live from a running
`taso-ventures/freeciv-llm` stack (via the proxy `:8002/llmsocket`) during Issue #6 validation.

It documents the **real runtime shape** produced by `civcom.build_llm_optimized_state`, which
differs from the documented `state_extractor._format_llm_optimized_state`:
`strategic.score` (not `victory_progress.current_score`), `economic.gold`/`economic.research`
(not `economic.resources.{gold,science}`), `tactical.active_units`/`visible_threats`. The
adapter handles **both** shapes (see `adapter.facts_from_state` / `_present`); this sample is
the regression anchor. Captured at turn 0 (pregame) — units/cities empty because the game
had not advanced past T000 (game-start orchestration is a freeciv-llm concern).
