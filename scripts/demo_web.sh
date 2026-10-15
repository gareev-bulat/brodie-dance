#!/usr/bin/env bash
# The website (landing page, email sign-in, Brodie's line): http://127.0.0.1:8765. Lessons are FaceTime only (the worker).
. "$(dirname "$0")/demo_env.sh" || exit 1
exec $PY -m brodie.server
