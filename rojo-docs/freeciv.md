# FreeCiv — OmegaClaw integration, benchmarks & PLN-vs-LLM experiments

**Single source of truth for all FreeCiv work in this repo.** Covers the integration fixes
(issues #6, #25), the benchmark harness, and the experiments testing whether OmegaClaw's PLN
symbolic reasoning improves play vs a plain LLM. All FreeCiv code + data live under
`benchmarks/freeciv/`. Detailed per-issue change reports are linked at the end.

**Timeline:** 2026-07-06 → 2026-07-09 · **Branch:** `exp/freeciv-ab-pln-vs-llm` (experiments) ·
integration work on `feat/freeciv-adapter` (#6) and `feat/freeciv-turn-cycle` (#25).

---

## 0. What this is
FreeCiv is used as a **live decision-making benchmark** for OmegaClaw. The agent connects to the
`taso-ventures/freeciv-llm` stack (an AGPL project at `~/Repos/freeciv-llm`) as a human player over
WebSocket, observes the game state, and chooses actions each turn. This lets us ask a concrete
question: **does OmegaClaw's PLN/MeTTa reasoning make an LLM play better than the same LLM alone?**

> **AGPL boundary:** `freeciv-llm` is never vendored into the MIT core — we only run against it as an
> external service. The SNET provider key lives in `.env` (gitignored).

### Stack at a glance
- `taso-ventures/freeciv-llm`: `docker compose up -d fciv-net` → civserver + `freeciv-proxy` on `:8002`.
- Agents connect over `ws://localhost:8002/llmsocket/8002`.
- Provider: SNET `openai/gpt-oss-120b` (via `provider_config`, key from `.env`).
- Reasoning engine: OmegaClaw's MeTTa/PLN on PeTTa, run **in-container** (`omegaclaw:local` image).

---

## 1. Integration fixes

### Issue #6 — deterministic state→atoms adapter + action validation
Turns raw `llm_optimized` game states into deterministic PLN atoms and gates every candidate action
through `validate_action` before it reaches the server. KPI benchmark over 6 schema-grounded fixture
states (`python3 benchmarks/freeciv/benchmark.py`):

| Metric | baseline (raw text, no gate) | candidate (atoms + gate) |
| --- | --- | --- |
| States converted to atoms | 0/6 | **6/6** |
| Mean field coverage | 0.00 | **1.00** |
| **Invalid-action submission rate** | **1.00** | **0.00** |
| Legal-action acceptance | n/a | 1.00 |
| Deterministic (2 runs identical) | n/a | **True** |

The candidate converts every state to deterministic atoms and blocks **100%** of illegal actions
while accepting all legal ones. Detail: [`benchmarks/freeciv/docs/issue-6-freeciv-adapter.md`].

> **Runtime-shape caveat (found live):** the real `civcom.build_llm_optimized_state` differs from the
> documented `state_extractor` — `strategic.score`/`economic.gold`/`research` rather than
> `victory_progress.current_score`/`economic.resources.*`. `player.score` reads **-1** and
> score/gold/science log as **0** live. `cities/units/techs/turns` parse correctly, so all experiment
> verdicts rest on those. See [`benchmarks/freeciv/samples/README.md`].

### Issue #25 — turn-cycle / `end_turn` handshake
The game would not advance past turn 1. Root cause: the proxy's `message_validator` requires the
action nested under a top-level `action` dict; the old envelope put `action_type` at top level and
was rejected with `E220` (never producing `PACKET_PLAYER_PHASE_DONE`, pid 52). KPI benchmark
(`python3 benchmarks/freeciv/turn_cycle_benchmark.py`):

| Metric | baseline `{type,action_type}` | candidate `{type,action:{action_type}}` |
| --- | --- | --- |
| Turns advanced (of 5) | **0** | **5** |
| Reached turn (from 1) | 1 | 6 |
| Monotonically increasing | False | **True** |

Fix = the `client.end_turn_message()` nested envelope. Also required: `player_ready` on all human
players before `/start`. Detail: [`benchmarks/freeciv/docs/issue-25-freeciv-turn-cycle.md`].

---

## 2. The benchmark harness
All under `benchmarks/freeciv/`:
- **Adapter/atoms:** `adapter.py`, `atoms.py` (state→facts→PLN atoms), `freeciv_tool.py`.
- **Reasoning bridge:** `reason.py` (`derive(facts)` runs the real PLN engine in-container),
  `rules.metta` (the FreeCiv PLN rules).
- **Shared agent:** `llm_agent.py` (`decide()` calls SNET; `render_plain()` = plain facts; the PLN
  arm appends `reason.format_for_llm(...)`).
- **Parallel A/B:** `ab_sim.py` / `ab_report.py` / `ab_run.sh` (each arm in its own game).
- **Head-to-head duel:** `duel_sim.py` / `duel_report.py` / `duel_run.sh` (both arms as opposing
  players in one 1v1 game; mirror-pair support).
- **Metrics:** `metrics.py`. **Host tests:** `Autotests/test_freeciv_ab.py`,
  `test_freeciv_turn_cycle.py` (mock WS).

### The PLN reasoning is real (not string formatting)
`(|~ fact rule)` fires lib_pln's Modus Ponens: e.g. `((Inheritance City_1 Undefended) (stv 1.0
0.99))` + the Undefended rule → `((Recommend City_1 Defend) (stv 0.9 0.71))`, the rule variable
unifying with the entity. Gate-verified in-container: Defend / Food / Settle rules ground correctly
(Retreat's Evaluation form does not fire under current lib_pln clauses — documented, kept for future
work). Live: ~1.3–2 recommendations/turn at ~220 ms. PLN word-form inference uses `|~` (lib_pln),
**not** `|-` (arrow-form NAL).

---

## 3. Experiment: does PLN reasoning improve play?

### Hypothesis
Augmenting an LLM's per-turn state view with **symbolic PLN-derived recommendations** produces
better FreeCiv play than the *same* LLM given raw facts alone (more cities / faster tech / better
survival). Null: the PLN block makes no reliable difference.

### Controls (held identical across arms)
Model (SNET `gpt-oss-120b`), temperature 0.3, action schema, pre-submit `validate_action`, map/game
seed, the #25 turn handshake, and the `omegaclaw:local` image. **The only variable is what the LLM
sees:** `plain` = plain-text state; `pln` = the same state **plus** a PLN-derived recommendation
block.

### 3a. Parallel A/B (each arm vs AI in its own game)
Two same-seed runs. **Run 2** (`ab_runs/20260708T143034Z`) is the first fully clean dataset
(0 reconnects, 0 LLM errors, 257 turns):

| Metric | pln | plain | winner |
|---|---|---|---|
| Peak cities | 1 | **3** | plain |
| Peak techs | 8 | **11** | plain |
| Peak units | 21 | **29** | plain |
| Final (t258) | 0/0 (**eliminated**) | 1 city | plain |
| Actions submitted | **490** | **754** | — |
| Illegal-action rate | 0.00 | 0.00 | tie |

Plain developed better and survived longer. First real lead: **PLN submitted far fewer actions
(490 vs 754)** — the recommendations seemed to *narrow* rather than enrich the action set.
(Run 1, `ab_runs/20260707T082249Z`, plateaued at turn 475 with a similar direction but had a
score-extraction gap; superseded by run 2.)

### 3b. Head-to-head duel — mirror pair (arms as opposing players)
`duel_sim.py` puts both arms in the **same 1v1 game**. A **mirror pair** swaps which slot is PLN
(g1: PLN=side0; g2: PLN=side1) to cancel start-position bias. Constraint discovered: the `:8002`
proxy carries **only one active game at a time**, so the mirror games run **sequentially** on a fresh
stack, not concurrently. Run `ab_runs/duel_20260708T150047Z`, seed 42, size 2; both clean:

| Game | PLN slot | PLN c/u/t | plain c/u/t | plateau | winner |
|---|---|---|---|---|---|
| g1 | side 0 | 3 / 21 / 32 | 7 / 69 / 33 | 879 | **plain** |
| g2 (mirror) | side 1 | 2 / 27 / 15 | 3 / 33 / 19 | 632 | **plain** |

**Plain won BOTH slots** → not a position artifact; a genuine, direction-consistent PLN disadvantage
*in this configuration*.

### 3c. Root cause: the PLN block *anchors* the LLM to fewer actions
Both arms are told "choose **1–3** actions." The PLN arm additionally gets a block headed
`DERIVED (PLN reasoning) — recommended priorities this turn:`. **The LLM treated that list as an
exhaustive to-do list** — doing exactly as many actions as there were recommendations, then stopping:

| | avg actions/turn | actions == recs |
|---|---|---|
| g1 PLN (side 0) | 2.06 | **97%** (863/881) |
| g2 PLN (side 1) | 1.63 | **99%** (632/638) |
| plain (both) | ~2.95 | — |

That ~30–45% per-turn activity deficit compounds over hundreds of turns into far less expansion (PLN
froze at 2–3 cities; plain reached 7). **The reasoning is correct; the *injection* was the problem** —
the block narrowed the LLM instead of augmenting it. (Secondary: the rule vocabulary — Defend / Food /
Settle-in-place — is consolidation-biased with no expansion signal.)

### 3d. The fix and the reversal
**Fix A** (commit `9b46234`, `reason.py::format_for_llm`): reframe the block header to
*"optional strategic hints (NOT a to-do list)"* and append *"Hints only — still choose the FULL 1–3
actions using your own judgment, including expansion such as founding new cities with settlers."*
Shared system prompt and rules left untouched, so the rerun cleanly isolates the reframing. Rerun:
`ab_runs/duelfix_20260708T195741Z`, seed 42, size 2.

The fix removed the anchoring exactly as predicted, and the head-to-head **flipped**:

| | old block ("priorities") | hints reframe (Fix A) |
|---|---|---|
| PLN actions/turn | 2.06 (anchored, **97%**) | **~2.9** (free, ~1–12%) |
| g1 slot (PLN=side0) | plain wins **7–3** | **tied 3–3** |
| g2 slot (PLN=side1) | plain wins **3–2** | **PLN wins 6–3** (73 vs 28 techs) |

The g2 mirror was a clean single game to turn 890 (no reset, 5 reconnects): **PLN 6 cities / 30
units / 73 techs vs plain 3 / 26 / 28** — the first time any PLN arm broke past 3 cities, retiring
the "shared 3-city ceiling" reading. (Fixed g1 had one mid-game server reset → two clean epochs,
both showing a 3–3 tie.)

### Verdict
The earlier **"PLN hurts" finding was largely an artifact of *how* recommendations were injected, not
the reasoning itself.** A one-line reframing (checklist → optional hints, preserve the action budget)
turned *plain wins both slots* into *PLN ties one slot and wins the other*. This is a **strong,
direction-consistent signal — not yet a statistical claim.**

**Honest caveats:** one seed per slot; fixed-g1 had a mid-game server reset; `score/gold/science`
are proxy-unavailable (verdicts rest on cities/units/techs/survival); the PLN treatment is still
one-hop rules.

---

## 4. Reproduce
```bash
# Stack (hard recreate clears stale ephemeral saves → fresh turn-1 games)
cd ~/Repos/freeciv-llm && docker rm -f fciv-net && docker compose up -d fciv-net   # wait :8002 -> 200

cd <worktree> && set -a; . ./.env; set +a          # SNET key

# Parallel A/B (seed 42, 1h cap, 600 turns)
bash benchmarks/freeciv/ab_run.sh 42 1 600
python3 benchmarks/freeciv/ab_report.py benchmarks/freeciv/ab_runs/<ts> --final

# Head-to-head duel mirror pair (seed, hours, max-turns, map size)
bash benchmarks/freeciv/duel_run.sh 42 6 5000 2     # NB: g2 mirror must run sequentially (proxy 1-game limit)
python3 benchmarks/freeciv/duel_report.py benchmarks/freeciv/ab_runs/duel_<ts> --final

# KPI micro-benchmarks (host, no stack needed)
python3 benchmarks/freeciv/benchmark.py             # #6 adapter/validation
python3 benchmarks/freeciv/turn_cycle_benchmark.py  # #25 turn cycle
```

---

## 5. Follow-ups
1. **Fix B — expansion vocabulary** in `rules.metta` (needs an adapter fact): see whether PLN can
   *widen* the lead now that anchoring is gone. Plan: `benchmarks/freeciv/docs/anchoring-fix-plan.md`.
2. **Multiple seeded pairs** (e.g. 10 seeds) to turn the direction-consistent signal into statistics.
3. **Fix `score/gold/science` extraction** against the real runtime `llm_optimized` shape.
4. **Deeper, decision-changing PLN rules** (multi-condition, threat-response, tech-path planning).
5. **Stability:** the mid-game server reset (turn→1) and the desktop-sleep interruptions warrant a
   more robust host / a proxy-side fix for unattended long runs.

---

## Appendix — detailed change reports (historical, co-located with code)
- [`benchmarks/freeciv/docs/issue-6-freeciv-adapter.md`] — #6 adapter + validation, full report.
- [`benchmarks/freeciv/docs/issue-25-freeciv-turn-cycle.md`] — #25 turn-cycle, full report.
- [`benchmarks/freeciv/docs/anchoring-fix-plan.md`] — the anchoring fix plan (Fix A / Fix B).
- KPI result files: `benchmarks/freeciv/results.md` (#6), `benchmarks/freeciv/turn_cycle_results.md` (#25).
- Run artifacts: `benchmarks/freeciv/ab_runs/<ts>/comparison.{md,json}` and `duel*/duel_comparison.md`.
