# Experiment — OmegaClaw PLN reasoning vs plain LLM (FreeCiv A/B)

**Branch:** `exp/freeciv-ab-pln-vs-llm` · **Run:** `ab_runs/20260707T082249Z`, seed 42 · 2026-07-07

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
