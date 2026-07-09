# Experiment — OmegaClaw PLN reasoning vs plain LLM (FreeCiv A/B)

**Branch:** `exp/freeciv-ab-pln-vs-llm` · **Runs:** `ab_runs/20260707T082249Z` (run 1) and
`ab_runs/20260708T143034Z` (run 2, clean) · seed 42 · 2026-07-07 → 2026-07-08

> **Latest, cleanest result first — see [Run 2](#run-2--clean-0-reconnects-2026-07-08) below.**
> Run 2 is the first fully clean same-seed A/B (0 reconnects, 0 LLM errors, desktop kept awake).
> In it the **plain LLM out-developed the PLN arm** (3 vs 1 peak cities, 11 vs 8 techs) and the
> **PLN arm was eliminated first (turn 258)**. This is a single seed and both arms played weakly, so
> it is directional, not conclusive — but it does **not** support the hypothesis that PLN helps here.

## Hypothesis
Augmenting an LLM's per-turn state view with **symbolic PLN-derived recommendations** (OmegaClaw's
differentiator) produces **better FreeCiv play** than the *same* LLM given the raw facts alone —
measurable as higher score / more cities / faster tech / better survival. Null hypothesis: the PLN
block makes no reliable difference (formatting-only, or too shallow to change decisions).
This was a **parallel A/B** (two separate games); the arms never interacted.

## Setup (for reproducibility)
- **Stack:** `taso-ventures/freeciv-llm` at `~/Repos/freeciv-llm` (`docker compose up -d fciv-net`),
  civserver + freeciv-proxy on `:8002`. Each arm connects over `ws://localhost:8002/llmsocket/8002`
  as a human player and drives turns via the #25-corrected `action` envelope.
- **Two arms, one variable** — what the LLM sees each turn:
  - **plain** — a plain-text summary of the state (units w/ type+pos, cities, gold/science/score, techs).
  - **pln** — the same summary **plus** a "DERIVED (PLN reasoning)" block from authentic in-container
    MeTTa/PLN inference (`benchmarks/freeciv/rules.metta` + `reason.py`).
- **Held identical:** model (SNET `gpt-oss-120b`), temperature (0.3), action schema, pre-submit
  `validate_action`, **map/game seed (42)**, nation (Romans), `aifill 3` opponents, the #25
  turn-advance handshake, and the `omegaclaw:local` image. Separate `game_id`s → two independent
  civserver games on the same seed (same map).
- **Bounds:** up to 10 h wall-clock / 5000 turns, whichever first (it ended far sooner at the
  turn-475 plateau). Per-turn JSONL metrics + heartbeat by `ab_sim.py`; snapshots/verdict by `ab_report.py`.

## The reasoning is real (not string formatting)
The pre-run gate proved genuine inference through OmegaClaw's PLN engine: `(|~ fact rule)` fires
lib_pln's Modus Ponens, e.g. `((Inheritance City_1 Undefended) (stv 1.0 0.99))` + the Undefended
rule → `((Recommend City_1 Defend) (stv 0.9 0.71))`, with the rule's variable unifying to the
entity. Verified in-container: Defend / Food / Settle rules ground correctly (Retreat's
Evaluation-form does not fire under lib_pln's Evaluation clauses — documented, kept for future work).
In the live run the pln arm derived ~1.3 recommendations/turn at ~226 ms.

## Result (both arms plateaued at turn 475)
Both games advanced cleanly to **turn 475** (474 turns), then **both** stopped advancing at the
*same* turn (repeated `no_advance`) — a shared server-side end/limit (identical seed+ruleset), not a
per-arm bug. Turn 475 is therefore a fair equal-plateau snapshot.

| Metric | pln (OmegaClaw+PLN) | plain (LLM only) | note |
|---|---|---|---|
| Turns advanced | 474 | 474 | tie — #25 handshake held for 474 turns |
| **Illegal-action rate** | **0.00** | **0.00** | Issue-#6 guarantee held on both arms |
| Cities | 1 | 2 | plain expanded more |
| Techs | 10 | 15 | plain teched faster |
| Units | **15** | **0** | plain was militarily wiped; pln preserved its army |
| Score / gold / science | 0 | 0 | **not captured** (see caveat) |
| Avg LLM ms | 1845 | 2970 | |
| Avg reason ms | 226 | — | pln only |
| PLN conclusions/turn | 1.3 | 0 | by design |
| LLM errors (recovered) | 0 | 2 | |

## Verdict: **inconclusive — no clear PLN advantage in this single run**
A crude metric-count favors plain (more cities + techs), but that is **not** a sound conclusion:
- **The real win metric (score) was never captured** — `score/gold/science` logged 0 for both the
  entire game. `techs/cities/units` read correctly, so the state was parsed, but the score/gold field
  paths didn't match this runtime `llm_optimized` shape. Without score we cannot say who was actually
  "winning."
- A genuine **behavioral divergence** did emerge from the identical map+model: **plain** optimized
  expansion/tech but ended with **0 units** (its military was annihilated); **pln** preserved a large
  army (15 units) but expanded/teched less. Neither dominates — it's a strategic-style difference, and
  arguably pln's surviving military is the healthier end-state that a score metric might have rewarded.
- **n = 1 seed** — directional only, not statistically conclusive.
- The PLN "treatment" was **shallow**: one-hop rules (situation→priority), mostly Settle
  recommendations; a modest prompt nudge, not deep multi-step planning.

**Honest bottom line:** this run does not provide evidence that PLN reasoning improves play, nor that
it hurts. It demonstrates a **working, reproducible A/B method** and that **authentic PLN inference
runs in the live loop** — but a real claim needs (a) score/gold extraction fixed, (b) many seeded
pairs, and (c) deeper/decision-changing rules.

## Run 2 — clean (0 reconnects) — 2026-07-08
**Run:** `ab_runs/20260708T143034Z`, seed 42, same matched controls as run 1. This is the first run
with the desktop kept awake the whole time, so it is the first **valid** dataset: **0 reconnects,
0 LLM errors, 0 illegal actions** on both arms across **257 advanced turns**. (Run 1 had a
score-extraction gap and a mid-run plateau; run 2 supersedes it for the head-to-head development
comparison, though the score/gold caveat below still applies to both.)

Both arms ran on the same seed until the **PLN player lost its last city+unit at turn 258** (arm
eliminated); the plain arm outlasted it (still 1 city). The sims were then stopped — with the PLN
player wiped, the turn could no longer be driven forward.

| Metric | pln (OmegaClaw+PLN) | plain (LLM only) | winner |
|---|---|---|---|
| Peak cities | 1 | **3** | plain |
| Peak techs | 8 | **11** | plain |
| Peak units | 21 | **29** | plain |
| Final (t258) cities / units | 0 / 0 (**eliminated**) | 1 / 0 | plain (survived) |
| Actions submitted | 490 | 754 | — |
| Illegal-action rate | 0.00 | 0.00 | tie |
| Turns advanced | 257 | 257 | tie |
| Avg LLM ms | 1646 | 3202 | — |
| Avg reason ms | 227 | — | pln only |
| PLN conclusions/turn | 1.9 | 0 | by design |
| Reconnects / LLM errors | 0 / 0 | 0 / 0 | tie |

**Run-2 verdict: plain LLM developed better and survived longer.** On every development axis
(cities, techs, units) plain led, and the PLN arm was the one eliminated. Caveats that keep this
from being a final claim: (a) **n = 1 seed** — one matched pair; (b) **both arms played poorly** —
1–3 cities at turn 250+ means the AI opponents dominated both, so this measures a weak FreeCiv
player more than it isolates PLN's marginal effect; (c) score/gold/science still unavailable (proxy
`player.score=-1`), so "development" rests on cities/techs/units/survival, not the game's own score;
(d) the PLN "treatment" is still one-hop (situation→priority), a modest prompt nudge. Notably the
PLN arm also **submitted far fewer actions (490 vs 754)** — a lead worth investigating: the derived
recommendations may be narrowing rather than enriching the LLM's action set.

**Combined bottom line (runs 1 + 2):** two same-seed pairs, no evidence that PLN *improves* play;
run 2 (the clean one) is mildly *against* the hypothesis. The harness and authentic in-loop PLN
inference are proven; a real claim still needs many seeded pairs, deeper decision-changing rules,
and ideally a stronger base agent that isn't getting crushed by the AI in the opening.

## Head-to-head duel (LLM+PLN vs LLM) — 2026-07-08
The parallel A/B has each arm in its *own* game vs the AI. The **duel** puts the two arms as
**opposing players in the SAME 1v1 game** (`duel_sim.py`), so they compete directly. To cancel
start-position bias we run a **mirror pair**: g1 with PLN as player slot 0, g2 (mirror) with PLN as
slot 1. Constraint discovered: the `:8002` LLM-proxy carries only **one active game at a time**, so
the mirror games can't run concurrently — g2 was run **sequentially** on a fresh stack after g1.
Both games were clean (g1: 11 reconnects over 879 turns; g2: 10 over 632), desktop kept awake.

**Run:** `ab_runs/duel_20260708T150047Z`, seed 42, size 2. Each game ended at a server plateau
(turn stops advancing, repeated `no_advance`) with **neither side eliminated**, so the winner is by
development at the plateau.

| Game | PLN slot | PLN cities/units/techs | plain cities/units/techs | plateau turn | winner |
|---|---|---|---|---|---|
| g1 | side 0 | 3 / 21 / 32 | 7 / 69 / 33 | 879 | **plain** |
| g2 (mirror) | side 1 | 2 / 27 / 15 | 3 / 33 / 19 | 632 | **plain** |

**Verdict: plain-LLM won BOTH games, on both player slots.** PLN did not win on either side, so the
result is **not** a start-position artifact — it is a genuine, direction-consistent disadvantage for
the PLN arm in this configuration.

### Root cause: the PLN block *anchors* the LLM to fewer actions
Investigating *why* PLN underperforms surfaced a clear, quantified mechanism. Both arms are told
"choose **1–3** actions"; the only difference is the PLN arm also gets a
`DERIVED (PLN reasoning) — recommended priorities this turn:` block. **The LLM treats that list as an
exhaustive to-do list** and does exactly as many actions as there are recommendations, then stops:

| | avg actions/turn | avg PLN recs/turn | actions == recs |
|---|---|---|---|
| g1 PLN (side 0) | 2.06 | 2.09 | **97%** (863/881) |
| g2 PLN (side 1) | 1.63 | 1.63 | **99%** (632/638) |
| g1 plain | 2.93 | 0 | — |
| g2 plain | 2.95 | 0 | — |

The plain arm freely uses its full budget (~3 actions/turn); the PLN arm anchors to the
recommendation count (~1.6–2.1). That **~30–45% per-turn activity deficit compounds** over hundreds
of turns into far less expansion — the PLN player froze at 2–3 cities early while plain climbed to
7. A secondary factor: the rule vocabulary (`rules.metta`) is consolidation-biased (Defend / Food /
Settle-in-place) with **no expansion signal**, so even the anchored priorities lean toward turtling.

**The reasoning is authentic and correct; the *injection* is the problem** — the block narrows the
LLM instead of augmenting it. Fix plan (`benchmarks/freeciv/docs/anchoring-fix-plan.md`): reframe the
block as *optional hints, not a checklist*, explicitly preserve the full 1–3 action budget, and add
an expansion signal — then rerun the duel and check that PLN's actions/turn rises to ≈3.

### Rerun with the anchoring fix (commit 9b46234) — 2026-07-08/09
Applied **Fix A only** (reframe the block to "optional strategic hints (NOT a to-do list)" + an
explicit "still choose the FULL 1–3 actions … including expansion"); left the shared system prompt
and the rules untouched so the rerun cleanly isolates the reframing. Run:
`ab_runs/duelfix_20260708T195741Z`, seed 42, size 2.

**The fix removed the anchoring, exactly as predicted:**

| | avg actions/turn | proposed == recs |
|---|---|---|
| PLN **before** (old block) | 2.06 | **97%** |
| PLN **after** (hints reframe) | **2.87–2.96** | **0–2%** |
| plain (unchanged) | ~2.95 | — |

PLN now uses its full action budget on ~every turn, matching the plain arm. **And the head-to-head
gap collapsed** (g1, PLN=side0; the run had one mid-game server reset → two clean epochs, both
reported):

| Game/epoch | PLN cities/units/techs | plain cities/units/techs | cities |
|---|---|---|---|
| old-code g1 | 3 / 21 / 32 | 7 / 69 / 33 | plain **+4** |
| fixed g1 epoch 1 (→t751) | 3 / 28 / 21 | 3 / 35 / 25 | **tied** |
| fixed g1 epoch 2 (→t890) | 3 / 28 / 25 | 3 / 38 / 21 | **tied** |

**Fixing the injection turned a plain blowout (7–3 cities) into a tie (3–3).** PLN no longer loses
on cities; a modest unit gap (~28 vs 35–38) remains and techs split (PLN ahead in epoch 2). Both
arms now plateau at 3 cities — consistent with a shared map/base-agent expansion ceiling rather than
a PLN-specific deficit. Caveats: (a) the mid-game reset means g1 is two ~short games, not one long
one; (b) the mirror game **g2 (PLN=side1) is still running** — verdict updated when it completes;
(c) still one seed. **Bottom line so far:** the earlier "PLN hurts" result was largely an artifact
of *how* the recommendations were injected, not the reasoning itself — once injected as hints, PLN
plays on par with the plain LLM here. Whether it can *exceed* it needs the expansion-rule work
(Fix B) and multiple seeds.

## What this experiment did establish
- A reproducible in-container A/B harness (`ab_sim.py`/`ab_report.py`/`ab_run.sh`) with matched
  controls and per-turn metrics.
- **Authentic** MeTTa/PLN inference in the decision loop (gate-verified derivations with truth values).
- The #25 turn-cycle fix and the #6 validation held under load: **474 turns, 0 illegal actions on
  both arms**.

## Reproduce
```
cd ~/Repos/freeciv-llm && docker compose up -d fciv-net       # stack healthy (:8002 -> 200)
cd <worktree> && bash benchmarks/freeciv/ab_run.sh 42 10 5000  # sources .env (SNET key), both arms
python3 benchmarks/freeciv/ab_report.py benchmarks/freeciv/ab_runs/<ts>          # 30-min snapshot
python3 benchmarks/freeciv/ab_report.py benchmarks/freeciv/ab_runs/<ts> --final  # comparison.{md,json}
```

## Follow-ups (to make it conclusive)
1. **Fix score/gold/science extraction** against the real runtime `llm_optimized` shape (biggest gap).
2. **Multiple seeded pairs** (e.g. 10 seeds) with aggregate stats.
3. **Deeper PLN rules** that change decisions (multi-condition, threat-response, tech-path planning).
4. **Investigate the turn-475 plateau** (ruleset endyear vs a late-game turn-cycle stall).
