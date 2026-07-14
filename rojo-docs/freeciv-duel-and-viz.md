# Running a FreeCiv duel & visualizing the results

A practical, step-by-step guide: how to run a head-to-head **OmegaClaw+PLN vs plain-LLM** duel,
and how to open the visualization webpage to explore the moves, stats, and the PLN player's
atomspace.

For the experiment's *findings* (why PLN anchors the LLM, the mirror-pair verdicts), see
[`freeciv.md`](freeciv.md). This doc is the **operator's manual**.

---

## What a "duel" is

Two identical LLM players compete as opposing humans in **one** FreeCiv game — same model, same
schema, same validation. The only difference: the PLN side's prompt carries MeTTa/PLN-derived
recommendations. Because start position matters, a duel is run as a **mirror pair**: two same-seed
games (`g1`, `g2`) that swap which player slot is PLN, so a real signal is "PLN wins *both*."

The winner of each game is decided by **elimination** (0 cities and 0 units), else **territory**
(cities > units > techs). `score/gold/science` are proxy-unavailable and always log as `0`.

---

## 1. Prerequisites

```bash
# 1) Bring up the FreeCiv stack (fresh turn-1 games; clears stale ephemeral saves).
cd ~/Repos/freeciv-llm && docker rm -f fciv-net && docker compose up -d fciv-net
#    Wait until the proxy answers on :8002 (HTTP 200) before launching a duel.

# 2) An LLM provider key must be in the repo's .env (SNET by default).
cd ~/Repos/OmegaClaw-Core
grep -q SNET_API_KEY .env && echo "key present" || echo "add SNET_API_KEY to .env"

# 3) The omegaclaw image (carries the PeTTa/PLN interpreter the PLN side needs).
#    Built once; override with OMEGACLAW_IMAGE if yours is named differently (default omegaclaw:local).
docker image inspect omegaclaw:local >/dev/null 2>&1 && echo "image present" || echo "build omegaclaw:local first"
```

> The PLN reasoning only runs **inside** the omegaclaw container (PeTTa/hyperon is not on the
> host). Running `duel_sim.py` directly on the host still works, but the PLN side derives no
> recommendations — always run duels via the container launcher below.

---

## 2. Run a duel

```bash
# bash benchmarks/freeciv/duel_run.sh [SEED] [HOURS] [MAX_TURNS] [SIZE]
bash benchmarks/freeciv/duel_run.sh 42 6 5000 2
```

Arguments (all optional, with the defaults shown):

| arg | default | meaning |
|-----|---------|---------|
| `SEED` | `42` | map + game seed (both games in the pair use it) |
| `HOURS` | `6` | wall-clock cap per game |
| `MAX_TURNS` | `5000` | turn cap per game |
| `SIZE` | `2` | map size (small ⇒ players contact early) |

The launcher creates a timestamped run dir and starts the mirror pair as two containers,
**staggered** (g2 waits until g1 reaches turn 1 — the proxy handles one pregame at a time):

```
benchmarks/freeciv/ab_runs/duel_<UTC-timestamp>/
  g1/   duel.jsonl  duel.heartbeat  duel_summary.json     # PLN = player slot 0
  g2/   duel.jsonl  duel.heartbeat  duel_summary.json     # PLN = player slot 1 (mirror)
```

It also writes `ab_runs/LATEST_DUEL` pointing at the new dir.

### Monitor progress

```bash
BASE=$(cat benchmarks/freeciv/ab_runs/LATEST_DUEL)

# Live per-game status + running mirror verdict:
python3 benchmarks/freeciv/duel_report.py "$BASE"

# Container logs:
docker logs -f fc-duel-g1-<timestamp>     # and fc-duel-g2-<timestamp>
```

A game ends on elimination, a sustained plateau, or the hour/turn cap. When both games finish,
each `g*/duel_summary.json` holds the final standing and winner.

### Finalize the tracked comparison

```bash
python3 benchmarks/freeciv/duel_report.py "$BASE" --final
```

This writes the committable `duel_comparison.{md,json}` (the durable record). It reads the raw
`duel.jsonl` when present and **fails closed** — it will never overwrite a committed comparison
with empty output. The raw `duel.jsonl` / `*_summary.json` are gitignored (large, container-owned);
`duel_comparison.{md,json}` is what you commit.

---

## 3. Visualize the results

A single self-contained webpage (`benchmarks/freeciv/viz/`) reads every run under `ab_runs/` and
lets you explore them in a browser. No build step, no dependencies.

```bash
bash benchmarks/freeciv/viz/serve.sh          # regenerates data, then serves
#   → open http://localhost:8009/
#   optional: bash benchmarks/freeciv/viz/serve.sh 9000   to pick a port
```

`serve.sh` runs two generators, then starts `python3 -m http.server`:

- `build_index.py` → `viz/data/index.json` — a catalog of every run (A/B and duel), normalizing
  the on-disk layouts into one page-friendly file. Reads raw `duel.jsonl` when present; otherwise
  falls back to the committed `comparison.json` / `duel_comparison.json`, so the page still works
  on a fresh checkout with no raw logs.
- `dump_atoms.py` → `viz/data/atoms.json` — the PLN player's atomspace, reconstructed from a
  captured state (default `samples/real_state_turn1.json`; `--state PATH` to use another).

> A browser cannot `fetch()` sibling files over `file://`, so the page **must be served** — open
> `index.html` through the local server, not by double-clicking it.

### What the page shows

- **Run picker** — every run, newest first; a game selector (`g1`/`g2`) for mirror pairs. A
  `· moves` tag marks runs that carry per-unit move detail.
- **Stats** — winner, final & peak cities/units/techs (PLN vs plain), avg actions/turn, avg PLN
  conclusions/turn, **% actions = recs** (the anchoring metric), illegal-action rate, and
  LLM/reasoning latency.
- **Moves over time** — per-turn territory trajectories, plus (for runs with per-turn detail)
  actions proposed/blocked, PLN conclusions, and latency. Every chart has a crosshair + tooltip.
- **Per-unit moves** — a per-turn table of each unit action (actor, action type, target, whether
  it was accepted, and whether PLN recommended that actor). See the note below.
- **Atomspace** — the PLN player's observed facts → rules → derived recommendations, drawn as a
  fact→rule→recommendation graph, with truth values on hover.
- **Static KPI micro-benchmarks** — the host-only `results.json` / `turn_cycle_results.json`.

### A note on per-unit moves

Per-unit move detail is logged by `duel_sim.py` and therefore appears **only for duels recorded
after move-logging was added**. Older runs show per-turn aggregates (counts + territory) — still
fully charted, just without the per-unit table. To capture moves, run a fresh duel (section 2).

### Regenerating without the browser

You can run the generators standalone at any time (e.g. after finalizing a new run):

```bash
python3 benchmarks/freeciv/viz/build_index.py    # refresh the run catalog
python3 benchmarks/freeciv/viz/dump_atoms.py      # refresh the atomspace snapshot
```

`viz/data/` is regenerated from run artifacts and is **gitignored** — the committed record stays
the compact `comparison.json` / `duel_comparison.json`.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Duel stuck at turn 0 | Proxy not ready, or a concurrent pregame. Confirm `:8002` returns 200; the launcher already staggers g2 — don't start two pairs at once. |
| PLN side derives nothing (`n_conclusions` = 0) | Running on the host instead of the container, or the image lacks PeTTa. Launch via `duel_run.sh`. |
| `duel_report --final` refuses to write | Raw `duel.jsonl` absent (gitignored) and a committed comparison exists — it fails closed by design. Run against a dir that still holds the raw logs. |
| Page is blank / "No index.json found" | Run `build_index.py` (or `serve.sh`, which does it for you); make sure you opened the served URL, not the `file://` path. |
| Page shows no per-unit moves | Expected for older runs — see the note above; run a fresh duel. |

See also: [`freeciv.md`](freeciv.md) (findings + reproduce), and
[`../benchmarks/freeciv/viz/README.md`](../benchmarks/freeciv/viz/README.md) (viz internals).
