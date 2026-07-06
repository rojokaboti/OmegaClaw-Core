#!/usr/bin/env bash
# Launch the PLN-vs-plain-LLM A/B run (Issue #25 experiment): two in-container arms in parallel.
#
# Both arms use the SAME model/provider (SNET via env), seed, nation, and validation; only the state
# representation differs (pln = plain facts + MeTTa/PLN recommendations; plain = plain facts only).
# The worktree is mounted OVER the baked repo so the container runs live code and MeTTa library
# imports resolve; --network host lets the container reach the freeciv stack on localhost:8002.
#
# Usage: bash benchmarks/freeciv/ab_run.sh [SEED] [HOURS] [MAX_TURNS]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"          # worktree root
cd "$REPO"

# LLM provider through the env (SNET_API_KEY etc.)
set -a; . ./.env; set +a
: "${SNET_API_KEY:?SNET_API_KEY not set in .env}"

SEED="${1:-42}"; HOURS="${2:-10}"; MAX_TURNS="${3:-2000}"
IMAGE="${OMEGACLAW_IMAGE:-omegaclaw:local}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_HOST="$REPO/benchmarks/freeciv/ab_runs/$TS"
OUT_CTR="/PeTTa/repos/OmegaClaw-Core/benchmarks/freeciv/ab_runs/$TS"
mkdir -p "$OUT_HOST"
echo "run dir: $OUT_HOST   seed=$SEED hours=$HOURS max_turns=$MAX_TURNS image=$IMAGE"

launch () {  # arm game_id
  local arm="$1" gid="$2"
  docker run -d --name "fc-ab-${arm}-${TS}" --network host \
    --entrypoint bash \
    -v "$REPO":/PeTTa/repos/OmegaClaw-Core \
    -e SNET_API_KEY -e FREECIV_PROVIDER="${FREECIV_PROVIDER:-SNET}" \
    -e FREECIV_PROXY_WS="ws://localhost:8002/llmsocket/8002" \
    -e OMEGACLAW_METTA_CMD -e OMEGACLAW_METTA_CWD -e OMEGACLAW_REASON_IMPORTS \
    "$IMAGE" -lc "pip install --break-system-packages -q websockets >/dev/null 2>&1; \
      exec python3 /PeTTa/repos/OmegaClaw-Core/benchmarks/freeciv/ab_sim.py \
        --arm ${arm} --game-id ${gid} --seed ${SEED} --hours ${HOURS} \
        --max-turns ${MAX_TURNS} --out ${OUT_CTR}"
  echo "launched arm=${arm} container=fc-ab-${arm}-${TS}"
}

launch pln   "ab_pln_${TS}"
launch plain "ab_plain_${TS}"

echo "$OUT_HOST" > "$REPO/benchmarks/freeciv/ab_runs/LATEST"
echo
echo "Progress:  python3 benchmarks/freeciv/ab_report.py $OUT_HOST"
echo "Final:     python3 benchmarks/freeciv/ab_report.py $OUT_HOST --final"
echo "Logs:      docker logs -f fc-ab-pln-${TS}   |   docker logs -f fc-ab-plain-${TS}"
