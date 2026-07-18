#!/bin/sh
# Per-stack batch worker (runs INSIDE a docker:cli container so it survives host/session teardowns).
# Processes a queue of seeds SEQUENTIALLY on ONE isolated freeciv-llm stack instance. For each seed
# it runs the duel mirror pair (g1 PLN=side0, g2 PLN=side1) and the A/B pair (pln arm, plain arm),
# recreating the stack before EVERY game so each starts from a fresh turn-1 world (a plain restart
# reloads the previous ephemeral save; only rm+compose-up clears it). One game at a time — the proxy
# carries a single active game, so concurrency reconnect-storms.
#
# Args: $1=INST  $2=PROXY_PORT  $3="space separated seeds"
# Env : SNET_API_KEY, BATCH_REL, FREECIV_PROVIDER, DUEL_MAX_TURNS, AB_MAX_TURNS, GAME_HOURS
set -u
INST="$1"; PROXY_PORT="$2"; SEEDS="$3"
OMEGA=/home/rojo-dev/Repos/OmegaClaw-Core
STACK=/home/rojo-dev/Repos/freeciv-llm
WS="ws://localhost:$PROXY_PORT/llmsocket/8002"
DUEL_MT="${DUEL_MAX_TURNS:-250}"
AB_MT="${AB_MAX_TURNS:-250}"
HRS="${GAME_HOURS:-12}"
PROV="${FREECIV_PROVIDER:-SNET}"
P="[worker$INST]"
log() { echo "$P $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

recreate() {
  docker rm -f "fciv-net-$INST" >/dev/null 2>&1
  INST="$INST" PROXY_PORT="$PROXY_PORT" docker compose -p "fciv$INST" \
    -f "$STACK/docker-compose.yml" -f "$OMEGA/benchmarks/freeciv/batch/override.yml" \
    --project-directory "$STACK" up -d fciv-net >/dev/null 2>&1
  i=0
  while [ $i -lt 60 ]; do
    h=$(docker inspect --format '{{.State.Health.Status}}' "fciv-net-$INST" 2>/dev/null)
    [ "$h" = "healthy" ] && { sleep 20; return 0; }
    i=$((i + 1)); sleep 5
  done
  log "WARN stack instance $INST not healthy after recreate"; return 1
}

wait_gone() { while docker ps --filter "name=$1" --format '{{.Names}}' | grep -q "$1"; do sleep 30; done; }

sim() {  # $1=container_name  $2=in-container python cmd
  docker run -d --name "$1" --network host --entrypoint bash \
    -v "$OMEGA":/PeTTa/repos/OmegaClaw-Core \
    -e SNET_API_KEY -e FREECIV_PROVIDER="$PROV" -e FREECIV_PROXY_WS="$WS" \
    omegaclaw:local -lc "$2" >/dev/null 2>&1
}
report() {  # $1=script  $2=dir
  docker run --rm --network host -v "$OMEGA":/PeTTa/repos/OmegaClaw-Core --entrypoint bash \
    omegaclaw:local -lc "cd /PeTTa/repos/OmegaClaw-Core && python3 benchmarks/freeciv/$1 $2 --final" >/dev/null 2>&1
}

for SEED in $SEEDS; do
  SD="$BATCH_REL/seed$SEED"
  mkdir -p "$OMEGA/$SD/duel/g1" "$OMEGA/$SD/duel/g2" "$OMEGA/$SD/ab"
  log "=== seed $SEED START (duel_mt=$DUEL_MT ab_mt=$AB_MT) ==="

  recreate; log "seed $SEED duel g1 (PLN=side0)"
  sim "fc-b$INST-s$SEED-dg1" "exec python3 -u /PeTTa/repos/OmegaClaw-Core/benchmarks/freeciv/duel_sim.py --game-id b${INST}d1_$SEED --seed $SEED --pln-side 0 --hours $HRS --max-turns $DUEL_MT --size 2 --out /PeTTa/repos/OmegaClaw-Core/$SD/duel/g1"
  wait_gone "fc-b$INST-s$SEED-dg1"

  recreate; log "seed $SEED duel g2 (PLN=side1)"
  sim "fc-b$INST-s$SEED-dg2" "exec python3 -u /PeTTa/repos/OmegaClaw-Core/benchmarks/freeciv/duel_sim.py --game-id b${INST}d2_$SEED --seed $SEED --pln-side 1 --hours $HRS --max-turns $DUEL_MT --size 2 --out /PeTTa/repos/OmegaClaw-Core/$SD/duel/g2"
  wait_gone "fc-b$INST-s$SEED-dg2"
  report duel_report.py "$SD/duel"; log "seed $SEED duel report done"

  recreate; log "seed $SEED A/B pln arm"
  sim "fc-b$INST-s$SEED-abp" "exec python3 -u /PeTTa/repos/OmegaClaw-Core/benchmarks/freeciv/ab_sim.py --arm pln --game-id b${INST}ap_$SEED --seed $SEED --hours $HRS --max-turns $AB_MT --out /PeTTa/repos/OmegaClaw-Core/$SD/ab"
  wait_gone "fc-b$INST-s$SEED-abp"

  recreate; log "seed $SEED A/B plain arm"
  sim "fc-b$INST-s$SEED-abq" "exec python3 -u /PeTTa/repos/OmegaClaw-Core/benchmarks/freeciv/ab_sim.py --arm plain --game-id b${INST}aq_$SEED --seed $SEED --hours $HRS --max-turns $AB_MT --out /PeTTa/repos/OmegaClaw-Core/$SD/ab"
  wait_gone "fc-b$INST-s$SEED-abq"
  report ab_report.py "$SD/ab"; log "seed $SEED ab report done"

  log "=== seed $SEED DONE ==="
done
log "ALL SEEDS DONE"
