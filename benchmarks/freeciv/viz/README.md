# FreeCiv benchmark visualization

A single self-contained webpage to explore the PLN-vs-plain-LLM benchmark runs: moves over
time, per-run stats, and the OmegaClaw+PLN player's atomspace. No build step, no dependencies
(stdlib Python + vanilla-JS/SVG page).

## Quick start

```bash
bash benchmarks/freeciv/viz/serve.sh          # regenerates data, serves at http://localhost:8009
```

Then open <http://localhost:8009/>. (A browser can't `fetch()` over `file://`, so it must be
served — `serve.sh` runs the two generators below, then `python3 -m http.server`.)

## What it shows

- **Stats** — per-run KPI tiles: winner, final & peak cities/units/techs (PLN vs plain), avg
  actions/turn, avg PLN conclusions/turn, `% actions = recs` (the *anchoring* metric),
  illegal-action rate, LLM/reasoning latency.
- **Moves over time** — per-turn territory trajectories, plus (for runs with per-turn detail)
  actions proposed/blocked, PLN conclusions, and latency. Crosshair + tooltip on every chart.
- **Per-unit moves** — for runs recorded after move-logging was added (`duel_sim.py`), a
  per-turn table of each unit action (actor, action type, target, valid, PLN-recommended).
- **Atomspace** — the PLN player's observed facts → rules → derived recommendations, drawn as a
  fact→rule→recommendation graph, reconstructed offline from a captured state.

## Data generators (run by `serve.sh`, or standalone)

- `build_index.py` → `data/index.json`: scans `../ab_runs/`, normalizing every layout (A/B
  `comparison.json`, duel `g{1,2}/duel.jsonl`, old committed-only duel) into one catalog. Reads
  the raw (gitignored) `duel.jsonl` when present; falls back to the committed
  `comparison.json` / `duel_comparison.json` otherwise — so it still produces a useful page on a
  fresh checkout with no raw logs.
- `dump_atoms.py` → `data/atoms.json`: reconstructs the atomspace from a captured state
  (default `../samples/real_state_turn1.json`, override with `--state PATH`) via
  `adapter → atoms → rules.metta`. Recommendations come from the real MeTTa/PLN engine
  (`reason.derive`) in-container; on the host it uses a rule-match fallback over the firing
  Inheritance rules (marked `source: host-fallback`).

`data/` is regenerated from run artifacts and is **gitignored** — the committed record stays the
compact `comparison.json` / `duel_comparison.json` files.

## Caveats surfaced in the UI

- `score`, `gold`, `science` are always `0` (proxy-unavailable); verdicts rest on
  cities/units/techs/survival.
- Older runs carry only per-turn aggregates — per-unit moves appear only on runs recorded after
  move-logging was added.
