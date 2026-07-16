# PLN-vs-LLM statistical batch harness

Runs many seeded repetitions of both FreeCiv experiments — the head-to-head **duel** (mirror pair)
and the **A/B** (each arm vs AI) — to turn the single-run signal into statistics.

## Why it's shaped this way
- **One game per proxy.** A freeciv-llm proxy carries a single active game; two concurrent games
  reconnect-storm each other. So each stack runs its games **sequentially**.
- **Parallelism = multiple stacks.** `batch.sh` stands up `N_STACKS` fully isolated stacks on ports
  `8002, 8012, 8022, …` (via `override.yml`: per-instance container names + remapped proxy port) and
  splits the seeds round-robin across them. Different stacks run in parallel.
- **Fresh world per game.** Every game recreates its stack's `fciv-net` (`rm -f` + `compose up`) so
  it starts at turn 1 — a plain `restart` reloads the previous ephemeral save and continues the old
  game.
- **Durable.** Workers are `docker:cli` containers, so the batch survives terminal/session teardown.

## Run
```bash
# defaults: N_STACKS=3, N_SEEDS=20, SEED_BASE=1001, DUEL_MAX_TURNS=250, AB_MAX_TURNS=250, GAME_HOURS=12
bash benchmarks/freeciv/batch/batch.sh
# tune, e.g. a quick 6-seed pass with shorter games:
N_SEEDS=6 DUEL_MAX_TURNS=150 AB_MAX_TURNS=150 bash benchmarks/freeciv/batch/batch.sh
```
Requires `.env` with `SNET_API_KEY`, the `omegaclaw:local` image, and the freeciv-llm stack at
`~/Repos/freeciv-llm` (override `FREECIV_LLM_DIR`).

## Layout produced
```
ab_runs/batch_<ts>/
  manifest.json
  seed<n>/duel/g1/duel.jsonl, duel/g2/duel.jsonl, duel/duel_comparison.{md,json}
  seed<n>/ab/{pln,plain}.jsonl, ab/comparison.{md,json}
```
`duel.jsonl`/`*.jsonl` are gitignored (large); the per-seed `comparison.*` and the batch
`aggregate.*` are the tracked records.

## Monitor & aggregate (works on partial batches)
```bash
docker ps --filter name=fc-worker            # workers
docker logs -f fc-worker-1-<ts>              # a worker's progress
python3 benchmarks/freeciv/batch/aggregate.py ab_runs/batch_<ts>
```
`aggregate.py` scans every completed game, decides the territory winner (cities > units > techs), and
reports per experiment: N, PLN/plain/tie win counts, an exact two-sided **sign-test** p, and per-metric
mean Δ (pln−plain) with a paired **t-stat** + normal-approx p. Writes `aggregate.{md,json}`.

## Stop / resume
```bash
docker rm -f $(docker ps -q --filter name=fc-worker) $(docker ps -q --filter name=fc-b)   # stop
```
Completed seeds are already on disk. To resume the remaining seeds, relaunch a worker for the missing
seeds (same `BATCH_REL`), or start a fresh batch — `aggregate.py` can be pointed at either dir.

## Timing
Each turn is an LLM call per side, so games are slow (~1–2 min/turn for the 2-player duel). A full
20-seed batch (duel pair + A/B pair each) across 3 stacks is a multi-day run. Lower `*_MAX_TURNS` for
a faster first pass; aggregate incrementally as seeds complete.
