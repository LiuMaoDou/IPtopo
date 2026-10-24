#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
PYTHON_BIN="${IPTOPO_PYTHON:-python3}"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required; set IPTOPO_PYTHON to its path"'
if [ ! -d .venv ]; then "$PYTHON_BIN" -m venv .venv; fi
.venv/bin/python -c 'import sys; assert sys.version_info >= (3, 12), "Existing .venv must be recreated using Python 3.12+"'
.venv/bin/python -m pip install -r requirements.lock
(cd frontend && npm ci && npm run build)
exec .venv/bin/python -m uvicorn backend.app.main:app --host "${IPTOPO_HOST:-127.0.0.1}" --port "${IPTOPO_PORT:-8765}"
