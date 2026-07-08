#!/usr/bin/env bash
# Launch the head-to-head PLN-vs-plain-LLM duel as a MIRROR PAIR (Issue #25 experiment follow-up):
# two 1v1 games on the SAME seed, swapping which player slot is PLN, to control for start-position
# bias. Each game is one container running duel_sim.py (which opens both players' WS connections).
#
# Usage: bash benchmarks/freeciv/duel_run.sh [SEED] [HOURS] [MAX_TURNS] [SIZE]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
set -a; . ./.env; set +a
: "${SNET_API_KEY:?SNET_API_KEY not set in .env}"

SEED="${1:-42}"; HOURS="${2:-6}"; MAX_TURNS="${3:-5000}"; SIZE="${4:-2}"
IMAGE="${OMEGACLAW_IMAGE:-omegaclaw:local}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BASE="benchmarks/freeciv/ab_runs/duel_$TS"
mkdir -p "$REPO/$BASE/g1" "$REPO/$BASE/g2"
echo "duel run: $REPO/$BASE   seed=$SEED hours=$HOURS max_turns=$MAX_TURNS size=$SIZE"

launch () {  # container_suffix game_id pln_side out_subdir
  docker run -d --name "fc-duel-$1-$TS" --network host --entrypoint bash \
    -v "$REPO":/PeTTa/repos/OmegaClaw-Core \
    -e SNET_API_KEY -e FREECIV_PROVIDER="${FREECIV_PROVIDER:-SNET}" \
    -e FREECIV_PROXY_WS="ws://localhost:8002/llmsocket/8002" \
    -e OMEGACLAW_METTA_CMD -e OMEGACLAW_METTA_CWD -e OMEGACLAW_REASON_IMPORTS \
    "$IMAGE" -lc "exec python3 /PeTTa/repos/OmegaClaw-Core/benchmarks/freeciv/duel_sim.py \
      --game-id $2 --seed $SEED --pln-side $3 --hours $HOURS --max-turns $MAX_TURNS \
      --size $SIZE --out /PeTTa/repos/OmegaClaw-Core/$BASE/$4"
  echo "launched $1: game_id=$2 pln_side=$3 -> $BASE/$4"
}

# g1: PLN is player slot 0 ; g2 (mirror): PLN is player slot 1.
# STAGGER: start g1, wait until it reaches a populated turn, THEN start g2 — two 1v1 games starting
# their pregame simultaneously contend on the proxy and both fail, so we serialize the starts.
launch g1 "duel1_$TS" 0 g1
echo "waiting for g1 to reach turn 1 before starting g2 (avoids concurrent-pregame contention)..."
for i in $(seq 1 45); do
  sleep 4
  if [ -f "$REPO/$BASE/g1/duel.heartbeat" ] && grep -q '"turn"' "$REPO/$BASE/g1/duel.heartbeat" 2>/dev/null; then
    echo "g1 populated (turn seen after ~$((i*4))s) — launching g2"; break
  fi
done
launch g2 "duel2_$TS" 1 g2

echo "$REPO/$BASE" > "$REPO/benchmarks/freeciv/ab_runs/LATEST_DUEL"
echo
echo "Progress: python3 benchmarks/freeciv/duel_report.py $REPO/$BASE"
echo "Logs:     docker logs -f fc-duel-g1-$TS   |   docker logs -f fc-duel-g2-$TS"
