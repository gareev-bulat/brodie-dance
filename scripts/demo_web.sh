#!/usr/bin/env bash
# The website (landing, accounts, the call page): http://127.0.0.1:8765. Mints LiveKit tokens and dispatches AGENT_NAME into each room.
. "$(dirname "$0")/demo_env.sh" || exit 1
exec $PY -m brodie.server
