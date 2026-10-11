#!/usr/bin/env bash
# Run a Verity character on Brodie's worker (facetime.worker), the prize-eligible runtime from the handbook, plus the web app
# (http://127.0.0.1:8765, which can call this character only; NO_WEB=1 skips it).
#   scripts/run_verity.sh [brodie|verity|dancity|gravity|polarity|intensity] [dev|start]
# Keys come from .env.local (LIVEKIT_*, GEMINI_API_KEY, GEMINI_BASE_URL, AGENT_NAME). Characters flagged lesson='dance' in
# verity/variants.py (Brodie, the kit-compliant demo character, and Dancity) run Bulat's dance lesson (--dance, starter/handlers.py;
# DANCE=<name> picks data/dances/<name>.npz, judged by Steven's model in starter/dance/judge.py) with their persona and face.
set -euo pipefail
cd "$(dirname "$0")/.."
CHAR="${1:-brodie}"; MODE="${2:-dev}"
_AGENT="${AGENT_NAME:-}"; set -a; source .env.local; set +a; [ -n "$_AGENT" ] && export AGENT_NAME="$_AGENT"   # a name set on the command line wins
# shellcheck disable=SC1091
source .venv/bin/activate
# "<voice> <lesson>" for the character ('Puck dance' for brodie, 'Aoede ' for verity); an unknown or secret character stops here
SPEC=$(python -c "from verity.variants import VARIANTS as V; import sys; k='$CHAR'; sys.exit('unknown character: '+k) if k not in V or V[k].get('secret') else print(V[k]['voice'], V[k].get('lesson', ''))")
read -r VOICE LESSON <<<"$SPEC"
export VERITY_CHARACTER="$CHAR"
if [ -z "${NO_WEB:-}" ]; then python -m verity.server & trap 'kill $! 2>/dev/null' EXIT; fi
COMMON=(--plan-file none --voice gemini --voice-name "$VOICE" --persona "starter/verity/$CHAR.md" --avatar "$CHAR" --text --log-caller)
if [ "${LESSON:-}" = dance ]; then
  # Bulat's lesson on Brodie's pose events. The creator's clip, the counts and the score cards are drawn into the coach's face track
  # (one video on the call; DANCE_VIDEO=0 for the face alone). --vision lets Dancity actually see the caller between parts (the
  # persona promises it) at a frame every 4 s: at 1 fps a 100 s call put ~100 images in the model's context, its replies slowed from
  # 1 s to 4 s and it started speaking its own reasoning aloud; --video-quality high keeps those frames sharp.
  # DANCE_PASS: the score that passes a part (the trained judge gave the creator's own clip 59% on a first try, so 70% was too
  # strict for a learner); DANCE=<name> picks data/dances/<name>.npz (default: the first one there).
  export DANCE_PASS="${DANCE_PASS:-0.5}"
  python -m facetime.worker "${COMMON[@]}" --dance --leave-grace-s -1 --vision --vision-fps "${VISION_FPS:-0.25}" --video-quality high "$MODE"
else
  # a conversation, not a workout: hear the caller over the voice (--barge-in) and see them sharp (--video-quality high; the low
  # simulcast layer is plenty for Brodie's pose model, not for "nice haircut"), one frame every 2 s (1 fps bloats the context, see above)
  python -m facetime.worker "${COMMON[@]}" --perception off --vision --vision-fps "${VISION_FPS:-0.5}" --video-quality high --barge-in --handlers starter/verity/handlers.py "$MODE"
fi
