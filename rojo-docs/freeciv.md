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

**#25 follow-up — live `submit_action` auth (was PR #34, folded in here).** The `freeciv-action`
tool path (`freeciv_tool.act()` → `client.py`) couldn't authenticate: it nested `api_token` under
`data` (the proxy requires it **top-level**, else `E220`) and targeted the llm-gateway
`/ws/agent` route instead of the proxy `/llmsocket/<port>` handler. Fixed in `client.py`
(`connect_message()` top-level token, `_ws_uri()` verbatim proxy endpoint, `from_env()` defaulting
to `/llmsocket/8002` and honoring `FREECIV_WS_URL`→`FREECIV_PROXY_WS`); `action_message()` now also
preserves a pre-shaped `actor_id`. Guarded by `Autotests/test_freeciv_client_ws.py` (6 tests pinning
the full `llm_connect`→`action` wire sequence and the E220 regression) plus an offline `client.py`
self-test, both wired into `run_mandatory`. (The A/B/duel sims were unaffected — they connect with
their own correct inline handshake and only borrow `client.action_message`.)

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

### 3d. The fix and the 1-1 swing
**Fix A** (commit `9b46234`, `reason.py::format_for_llm`): reframe the block header to
*"optional strategic hints (NOT a to-do list)"* and append *"Hints only — still choose the FULL 1–3
actions using your own judgment, including expansion such as founding new cities with settlers."*
Shared system prompt and rules left untouched, so the rerun cleanly isolates the reframing. Rerun:
`ab_runs/duelfix_20260708T195741Z`, seed 42, size 2.

The fix removed the anchoring exactly as predicted, and the head-to-head **swung from plain-wins-both
to a 1-1 split** (verified from committed artifacts — `duelfix_20260708T195741Z/duel_comparison.md`;
territory verdict = cities > units > techs):

| | old block ("priorities") | hints reframe (Fix A) |
|---|---|---|
| PLN actions/turn | 2.06 (anchored, **97%**) | **~2.9** (free, ~2–12%) |
| g1 slot (PLN=side0) | plain wins **7–3 cities** | cities **tied 3–3**; plain edges units 38–28 → narrow plain hold |
| g2 slot (PLN=side1) | plain wins **3–2 cities** | **PLN wins 6–3 cities** (73 vs 28 techs) |
| mirror aggregate | **plain 2 – 0** | **1 – 1 split** |

The g2 mirror was a clean single game to turn 890 (no reset, 5 reconnects): **PLN 6 cities / 30
units / 73 techs vs plain 3 / 27 / 28** — the first time any PLN arm broke past 3 cities, retiring
the "shared 3-city ceiling" reading. Fixed g1 had one mid-game server reset → two clean epochs; the
last epoch is **cities 3–3** with plain ahead on units (38 vs 28), so by the strict territory
tiebreak g1 is still a *narrow* plain hold — but a 3–3 city tie is a world away from the old 7–3
blowout.

### 3e. Reproduction (2026-07-14): fresh mirror pair — **PLN 2–0**
A clean re-run on a freshly-built stack (`taso-ventures/freeciv-llm` re-cloned; seed 42, size 2),
run as a **sequential** mirror. Run `ab_runs/duelseq_20260713T150716Z`, both games clean
(g1: 838 turns / 8 reconnects; g2: 1671 turns / 18 reconnects):

| Game | PLN slot | Plateau | PLN c/u/t | plain c/u/t | Winner |
|---|---|---|---|---|---|
| g1 | side 0 | 840 | 3 / 43 / 27 | 3 / 37 / 29 | **PLN** (units 43>37; cities tied 3–3) |
| g2 | side 1 | 1675 | 6 / 7 / 87 | 3 / 37 / 31 | **PLN** (cities 6>3, techs 87>31) |

Both arms acted at ~2.95 actions/turn with only **2–7%** of actions matching the recommendation
count (no anchoring — the Fix-A reframe holds). **PLN won BOTH slots** — a step up from the earlier
1–1 split, and g2's PLN played a striking tech/builder game (6 cities, 87 techs, few units).

**Reproduction notes.** The stack came up from a bare clone; getting a clean run required (a)
recreating the root-owned `logs/` bind-mount dir as writable (else every game server dies on launch),
(b) running the mirror games **sequentially** (the `:8002` proxy carries one active game at a time —
concurrent games reconnect-storm), and (c) a new pregame line **`/set timeout 0`** in `duel_sim.py`
(server waits for both players' `end_turn` instead of auto-advancing on a timer). Without (c) the game
auto-advances during pregame; if the sim hasn't latched onto turn 1 yet, the runaway turn-updates
flood `recv_until` past its drain and `get_state` never succeeds (sim hangs in setup). g1 above ran
*before* (c) was added — it self-paced identically by driving every turn, so gameplay is unbiased
(`timeout` only affects server pacing, not agent decisions); a perfectly symmetric mirror would re-run
g1 with the same line. Single seed / one pair — a strong signal, not a statistical claim.

### 3f. Statistical batch (2026-07-16 → 17): 20 seeds — **null result**
The single/mirror runs above were underpowered (first PLN 2–0, then 1–1 — small-sample noise). To
settle it, a parallel batch harness (`benchmarks/freeciv/batch/`) ran **20 seeded repetitions of BOTH
experiments** across 3 isolated freeciv-llm stacks (ports 8002/8012/8022), each stack running its
seeds sequentially with a fresh turn-1 recreate per game. Run `ab_runs/batch_20260716T081149Z`
(seeds 1001–1020, 250-turn cap; full stats in `aggregate.{md,json}`):

| Experiment | games | PLN wins | plain wins | sign-test p |
|---|---|---|---|---|
| Duel (mirror slots) | 40 | 21 | 19 | **0.87** |
| A/B (each vs AI) | 19* | 9 | 10 | **1.0** |

Per-metric mean Δ (pln − plain) over the 40 duel games (paired t, normal-approx p): cities **−0.20**
(p≈0.08), units **−1.25** (p≈0.24), techs **−0.68** (p≈0.39). *seed1011's A/B was incomplete (one
arm plateaued out) → 19 usable.

**Verdict — no measurable PLN effect.** Head-to-head win-rate is a coin flip (21–19, p=0.87), as is
A/B (9–10). No metric reaches significance: the persistent faint cities lean toward plain (−0.20/game)
is p≈0.08 *uncorrected* and **non-significant** after a 3-metric correction (~0.24). The earlier
"PLN 2–0" was noise. In the current **one-hop-rule** configuration, PLN recommendations neither help
nor hurt the LLM's play — consistent with the "rule vocabulary too thin to change outcomes" reading
below. A real test of PLN's value now needs **richer, decision-changing rules** (Fix B / deeper-rules
follow-up), not more seeds. Harness: `batch.sh` (stacks), `worker.sh` (durable per-stack conductor),
`aggregate.py` (sign test + paired t); per-turn/per-move detail browsable in `viz/`.

### Verdict
The earlier **"PLN hurts" finding was largely an artifact of *how* recommendations were injected, not
the reasoning itself.** A one-line reframing (checklist → optional hints, preserve the action budget)
moved the mirror from **plain 2–0** to a **1–1 split** — PLN's first outright win (g2, decisively),
and its other slot going from a 7–3 blowout loss to a 3–3 city tie. This is a **strong,
direction-consistent signal — not yet a statistical claim.**

**Honest caveats:** one seed per slot; fixed-g1 had a mid-game server reset (summarized on its last
epoch); the g1 "tie" is on cities (plain still edges units); `score/gold/science` are
proxy-unavailable (verdicts rest on cities/units/techs/survival); the PLN treatment is still
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

**Data package / regenerating comparisons.** The tracked record for each run is its
`comparison.{md,json}` (duel: `duel_comparison.{md,json}`), computed by the reporters from the raw
per-turn `*.jsonl` (gitignored — large). The reporters extract the *final* standing robustly (last
epoch, skipping the plateau tail — see `run_summary.py`). `--final` **fails closed**: if the raw logs
are absent it reprints / reuses the committed comparison and refuses to overwrite it with empty
output, so a fresh checkout can never clobber the tracked results. To fully regenerate from scratch,
re-run against a dir that still holds the raw `*.jsonl`.

**Visualize.** A self-contained webpage (`benchmarks/freeciv/viz/`) explores every run — moves
over time, per-run stats, and the PLN player's atomspace (facts → rules → recommendations):
```bash
bash benchmarks/freeciv/viz/serve.sh            # regenerates data, serves http://localhost:8009
```
It reads raw `duel.jsonl` when present and falls back to the committed `comparison.json` /
`duel_comparison.json` otherwise. Per-unit moves (unit, action, target, PLN-recommended flag) are
logged by `duel_sim.py` and appear for runs recorded after that logging was added; the atomspace is
reconstructed offline from a captured state via `dump_atoms.py`. See `benchmarks/freeciv/viz/README.md`.
For a step-by-step operator's guide (run a duel end-to-end, then visualize it), see
[`freeciv-duel-and-viz.md`](freeciv-duel-and-viz.md).

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
