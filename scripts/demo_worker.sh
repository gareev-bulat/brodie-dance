#!/usr/bin/env bash
# Dancity on Brodie's worker (the same arguments as run.ps1): dance mode, Gemini voice Leda, the dancity persona and avatar.
. "$(dirname "$0")/demo_env.sh" || exit 1
VOICE=$($PY -c "from verity.variants import VARIANTS as V; print(V['$VERITY_CHARACTER']['voice'])")
exec $PY -m facetime.worker --plan-file none --voice gemini --voice-name "$VOICE" --persona "starter/verity/$VERITY_CHARACTER.md" \
  --text --log-caller --dance --leave-grace-s -1 dev   # no --avatar: the dance screen is the only video track
