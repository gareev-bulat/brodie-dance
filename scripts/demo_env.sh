#!/usr/bin/env bash
# Shared by the demo launchers: load .env.local (LF endings), pick the dance and character.
cd "$(dirname "$0")/.." || exit 1
if grep -q '<' .env.local 2>/dev/null; then echo "FILL IN .env.local FIRST (LiveKit url/key/secret + Gemini token)"; exit 1; fi
set -a; . ./.env.local; set +a
export AGENT_NAME="${AGENT_NAME:-team-verity-agent}"
export VERITY_CHARACTER="${VERITY_CHARACTER:-dancity}"
export DANCE="${DANCE:-sbbyj}"
export PATH="$HOME/.local/bin:$PATH"
PY=~/.venvs/brodie/bin/python
