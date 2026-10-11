#!/usr/bin/env bash
# Run a Verity character on Brodie's worker (facetime.worker), the prize-eligible runtime from the handbook, plus the web app
# (http://127.0.0.1:8765, which can call this character only; NO_WEB=1 skips it).
#   scripts/run_verity.sh [verity|dancity|gravity|polarity|intensity] [dev|start]
# Keys come from .env.local (LIVEKIT_*, GEMINI_API_KEY, GEMINI_BASE_URL, AGENT_NAME). Dancity runs Bulat's dance lesson
# (--dance, starter/handlers.py; DANCE=<name> picks data/dances/<name>.npz) with Dancity's persona and face.
set -euo pipefail
cd "$(dirname "$0")/.."
CHAR="${1:-verity}"; MODE="${2:-dev}"
_AGENT="${AGENT_NAME:-}"; set -a; source .env.local; set +a; [ -n "$_AGENT" ] && export AGENT_NAME="$_AGENT"   # a name set on the command line wins
# shellcheck disable=SC1091
source .venv/bin/activate
VOICE=$(python -c "from verity.variants import VARIANTS as V; import sys; k='$CHAR'; sys.exit('unknown character: '+k) if k not in V or V[k].get('secret') else print(V[k]['voice'])")
export VERITY_CHARACTER="$CHAR"
if [ -z "${NO_WEB:-}" ]; then python -m verity.server & trap 'kill $! 2>/dev/null' EXIT; fi
COMMON=(--plan-file none --voice gemini --voice-name "$VOICE" --persona "starter/verity/$CHAR.md" --avatar "$CHAR" --text --log-caller)
if [ "$CHAR" = dancity ]; then
  python -m facetime.worker "${COMMON[@]}" --dance --leave-grace-s -1 "$MODE"
else
  python -m facetime.worker "${COMMON[@]}" --perception off --vision --handlers starter/verity/handlers.py "$MODE"
fi
