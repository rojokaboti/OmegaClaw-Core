# PLN anchoring fix — plan (to apply AFTER g2 mirror finishes)

## Finding (from g1, 879 turns; g2 mirror reproducing it)
The PLN "DERIVED (PLN reasoning) — recommended priorities this turn" block makes the LLM treat the
derived recommendations as an exhaustive to-do list: on **97% of turns the PLN arm proposed exactly
as many actions as it had recommendations** (avg 2.06 actions vs plain's 2.93). Both arms are told
"choose 1–3 actions"; plain freely uses 3, PLN anchors to ~2. That ~30%/turn activity deficit
compounds → PLN froze at 3 cities by turn 100 while plain reached 7. Secondary: the rule vocabulary
(Defend/Food/Settle-in-place) is consolidation-biased with no expansion signal.

## Fixes

### Fix A (primary) — reframe the PLN block as hints, decouple from action count
File: `benchmarks/freeciv/reason.py`, `format_for_llm()`.
- Change header from `"DERIVED (PLN reasoning) — recommended priorities this turn:"`
  to `"DERIVED (PLN reasoning) — optional strategic hints (NOT a to-do list):"`
- Append a trailing guidance line after the bullet list:
  `"(Hints only — do NOT limit yourself to these. Still choose the FULL 1–3 actions using your own"`
  `" judgment, including expansion like founding new cities with settlers, which the hints may omit.)"`
- Do NOT touch the shared `SYSTEM_PROMPT` (that would change the plain arm too and break the control).
  Only the PLN-arm block wording changes → the experiment still isolates "PLN-augmented" vs "plain".

### Fix B (secondary) — expansion signal in the rules
File: `benchmarks/freeciv/rules.metta` (+ `adapter.py`/`atoms.py` if a new fact is needed).
- Only if a fact indicating "should expand" can be emitted cleanly. The existing
  `Type_settlers → Settle` already recommends settling; the anchoring cap (Fix A) is the dominant
  cause, so Fix A is the must-have. Add expansion vocabulary only if low-risk.

## Rerun
After both fixes committed: hard-reset stack, relaunch the duel mirror pair (seed 42, size 2) with
the fixed code into a NEW run dir. Compare PLN actions/turn (expect ≈3, closing the gap) and cities.
Document in `rojo-docs/exp-freeciv-ab-pln-vs-llm.md`.
