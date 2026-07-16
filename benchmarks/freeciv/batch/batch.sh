#!/usr/bin/env bash
# Statistical batch runner for the PLN-vs-LLM FreeCiv experiments.
#
# Stands up N_STACKS isolated freeciv-llm stacks (ports 8002, 8012, 8022, ...), splits N_SEEDS seeds
# round-robin across them, and launches one durable docker:cli WORKER per stack. Each worker runs its
# seeds SEQUENTIALLY (proxy = one active game at a time); across stacks the seeds run in PARALLEL.
# Per seed a worker produces a duel mirror pair (g1/g2) and an A/B pair (pln/plain) with per-seed
# reports. Aggregate anytime with aggregate.py (works on partial results).
#
# Workers are containers, so the whole batch survives terminal/session teardowns. Re-running batch.sh
# starts a NEW batch dir; to resume a stopped batch just relaunch its workers (see README).
#
# Env: N_STACKS (default 3), N_SEEDS (default 20), SEED_BASE (default 1001),
#      DUEL_MAX_TURNS (250), AB_MAX_TURNS (250), GAME_HOURS (12).
set -euo pipefail
OMEGA="$(cd "$(dirname "$0")/../../.." && pwd)"
STACK="${FREECIV_LLM_DIR:-$HOME/Repos/freeciv-llm}"
cd "$OMEGA"
set -a; . ./.env; set +a
: "${SNET_API_KEY:?SNET_API_KEY not set in .env}"

N_STACKS="${N_STACKS:-3}"
N_SEEDS="${N_SEEDS:-20}"
SEED_BASE="${SEED_BASE:-1001}"
export DUEL_MAX_TURNS="${DUEL_MAX_TURNS:-250}"
export AB_MAX_TURNS="${AB_MAX_TURNS:-250}"
export GAME_HOURS="${GAME_HOURS:-12}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BATCH_REL="benchmarks/freeciv/ab_runs/batch_$TS"
mkdir -p "$OMEGA/$BATCH_REL"
SEEDS=$(seq "$SEED_BASE" $((SEED_BASE + N_SEEDS - 1)))

echo "batch $TS: N_STACKS=$N_STACKS N_SEEDS=$N_SEEDS seeds=[$SEED_BASE..$((SEED_BASE+N_SEEDS-1))] duel_mt=$DUEL_MAX_TURNS ab_mt=$AB_MAX_TURNS"
echo "batch dir: $OMEGA/$BATCH_REL"

# --- clean any prior stacks that would collide (default project + fciv1..N) ---
docker compose -p freeciv-llm -f "$STACK/docker-compose.yml" down >/dev/null 2>&1 || true
for inst in $(seq 1 "$N_STACKS"); do
  docker compose -p "fciv$inst" -f "$STACK/docker-compose.yml" -f "$OMEGA/benchmarks/freeciv/batch/override.yml" \
    --project-directory "$STACK" down >/dev/null 2>&1 || true
done

# --- manifest ---
python3 - "$OMEGA/$BATCH_REL/manifest.json" "$TS" "$N_STACKS" "$N_SEEDS" "$SEED_BASE" \
         "$DUEL_MAX_TURNS" "$AB_MAX_TURNS" <<'PY'
import json, sys
p, ts, nst, nse, base, dmt, amt = sys.argv[1:8]
json.dump({"ts": ts, "n_stacks": int(nst), "n_seeds": int(nse), "seed_base": int(base),
           "seeds": list(range(int(base), int(base)+int(nse))),
           "duel_max_turns": int(dmt), "ab_max_turns": int(amt)}, open(p, "w"), indent=2)
PY

# --- bring up stacks + launch workers ---
for inst in $(seq 1 "$N_STACKS"); do
  port=$((8002 + (inst - 1) * 10))
  myseeds=$(echo "$SEEDS" | awk -v n="$N_STACKS" -v k="$inst" 'NR % n == (k % n) {printf "%s ", $0}')
  echo "stack $inst on :$port  seeds=[$myseeds]"
  INST=$inst PROXY_PORT=$port docker compose -p "fciv$inst" \
    -f "$STACK/docker-compose.yml" -f "$OMEGA/benchmarks/freeciv/batch/override.yml" \
    --project-directory "$STACK" up -d fciv-net >/dev/null 2>&1

  docker rm -f "fc-worker-$inst-$TS" >/dev/null 2>&1 || true
  docker run -d --name "fc-worker-$inst-$TS" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$OMEGA":"$OMEGA" -v "$STACK":"$STACK" \
    -e SNET_API_KEY -e FREECIV_PROVIDER="${FREECIV_PROVIDER:-SNET}" \
    -e BATCH_REL="$BATCH_REL" -e DUEL_MAX_TURNS -e AB_MAX_TURNS -e GAME_HOURS \
    --entrypoint sh docker:cli "$OMEGA/benchmarks/freeciv/batch/worker.sh" "$inst" "$port" "$myseeds" >/dev/null 2>&1
  echo "  worker container: fc-worker-$inst-$TS"
done

echo "$BATCH_REL" > "$OMEGA/benchmarks/freeciv/ab_runs/LATEST_BATCH"
echo
echo "Launched. Monitor:  docker logs -f fc-worker-1-$TS"
echo "Aggregate anytime:  python3 benchmarks/freeciv/batch/aggregate.py $BATCH_REL"
