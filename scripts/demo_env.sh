#!/usr/bin/env bash
# Shared by the Brodie Dance launchers: load .env.local (LF endings), pick the dance and the persona.
cd "$(dirname "$0")/.." || exit 1
if grep -q '<' .env.local 2>/dev/null; then echo "FILL IN .env.local FIRST (LiveKit url/key/secret + Gemini token)"; exit 1; fi
set -a; . ./.env.local; set +a
export AGENT_NAME="${AGENT_NAME:-brodie-dance}"
export PERSONA="${PERSONA:-starter/persona.md}"   # Brodie the dance teacher
export DANCE="${DANCE:-sbbyj}"
export PATH="$HOME/.local/bin:$PATH"
PY=~/.venvs/brodie/bin/python
