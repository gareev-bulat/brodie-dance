#!/usr/bin/env bash
# Brodie the dance teacher on Brodie's worker: dance mode, Gemini voice, the persona in starter/persona.md, the lesson video track.
. "$(dirname "$0")/demo_env.sh" || exit 1
exec $PY -m facetime.worker --plan-file none --voice gemini --voice-name "${VOICE_NAME:-Puck}" --persona "$PERSONA" \
  --text --dance --leave-grace-s -1 dev
