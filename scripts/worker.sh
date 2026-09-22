#!/usr/bin/env sh
# Runs the Bevro worker on the host: the process that executes long-running
# provider runs (Claude Code) against approved workspaces.
#
#   ./scripts/worker.sh            # foreground; Ctrl-C stops it
#
# It runs on the host, not in Docker, because that is where the logged-in
# `claude` CLI and your project directories are. It needs the .venv created by
# docs/CODING_PROVIDER.md and the Docker stack (for PostgreSQL) to be up.
# Optional settings are read from .env next to docker-compose.yml.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] && { set -a; . ./.env; set +a; }
if [ ! -x .venv/bin/python ]; then
  echo "no .venv - run: python3 -m venv .venv && .venv/bin/pip install -r api/requirements.txt" >&2
  exit 1
fi
if ! mkdir -p data/artifacts data/logs 2>/dev/null || [ ! -w data ]; then
  echo "data/ is not writable by $(id -un). If Docker created it as root, run: sudo chown -R $(id -u):$(id -g) data" >&2
  exit 1
fi
# The worker talks to the same database and data directory as the Docker stack.
export BEVRO_DATABASE_URL="${BEVRO_WORKER_DATABASE_URL:-postgresql+psycopg://bevro:bevro@localhost:${BEVRO_DB_PORT:-6142}/bevro}"
export BEVRO_ARTIFACT_DIR="${BEVRO_WORKER_ARTIFACT_DIR:-$ROOT/data/artifacts}"
export BEVRO_LOG_DIR="${BEVRO_WORKER_LOG_DIR:-$ROOT/data/logs}"
export BEVRO_WORKSPACES_FILE="${BEVRO_WORKER_WORKSPACES_FILE:-$ROOT/config/workspaces.json}"
export BEVRO_SECRET_KEY="${BEVRO_SECRET_KEY:-}"
export BEVRO_SECRET_KEY_FILE="${BEVRO_WORKER_SECRET_KEY_FILE:-$ROOT/data/secret.key}"
export BEVRO_INTEGRATIONS_DIR="${BEVRO_WORKER_INTEGRATIONS_DIR:-$ROOT/data/integrations}"
export BEVRO_AGENTS_DIR="${BEVRO_WORKER_AGENTS_DIR:-$ROOT/data/agents}"
# BEVRO_LOCAL_ROOTS (folders Connect may inspect and run agents in) comes from .env as-is.
export BEVRO_LOCAL_ROOTS="${BEVRO_LOCAL_ROOTS:-}"
export PYTHONPATH="$ROOT:$ROOT/api"
# Never let the worker look like a nested Claude Code session to the CLI it starts.
unset CLAUDECODE
for v in $(env | grep -o '^CLAUDE_[A-Z_]*'); do unset "$v"; done
exec .venv/bin/python -m app.worker
