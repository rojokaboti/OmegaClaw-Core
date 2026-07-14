#!/usr/bin/env bash
# Serve the FreeCiv benchmark visualization page.
#
# A browser cannot fetch() sibling files over file://, so we serve viz/ over stdlib http.server.
# Regenerates the data first (index of all runs + a representative atomspace snapshot), then serves.
#
# Usage: bash benchmarks/freeciv/viz/serve.sh [PORT]   (default 8009)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-8009}"

python3 "$HERE/build_index.py"
python3 "$HERE/dump_atoms.py"

echo "Serving $HERE at http://localhost:$PORT/  (Ctrl-C to stop)"
exec python3 -m http.server "$PORT" --directory "$HERE"
