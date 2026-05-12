#!/bin/sh
export TALEMATE_DEBUG=1
uv run src/talemate/server/run.py runserver --host 0.0.0.0 --port "${TALEMATE_BACKEND_PORT:-5050}" --backend-only