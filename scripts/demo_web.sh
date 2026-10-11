#!/usr/bin/env bash
# The web app (http://127.0.0.1:8765): mints tokens for fresh rooms and dispatches AGENT_NAME into them.
. "$(dirname "$0")/demo_env.sh" || exit 1
exec $PY -m verity.server
