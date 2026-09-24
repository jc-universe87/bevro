#!/usr/bin/env sh
# The Bevro worker: the optional host process that runs work Docker cannot.
#
#   ./scripts/worker.sh            # foreground; Ctrl-C stops it
#
# You need this only for providers that live on this machine: a coding agent
# such as Claude Code, a local project you connected, or a command-line agent.
# Everything else - the built-in providers, HTTP and MCP services, scheduling,
# monitoring and notifications - works without it.
#
# It runs on the host rather than in a container because that is where your
# logged-in CLI tools and your project directories are.
#
# Prerequisites: the Docker stack up (for PostgreSQL), and a virtualenv:
#   python3 -m venv .venv && .venv/bin/pip install -r api/requirements.txt
# Optional settings are read from .env next to docker-compose.yml.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fail() {
  echo "" >&2
  echo "Bevro worker: $1" >&2
  shift
  for line in "$@"; do echo "  $line" >&2; done
  exit 1
}

# .env is optional and may contain secrets: it is loaded, never echoed.
[ -f .env ] && { set -a; . ./.env; set +a; }

[ -x .venv/bin/python ] || fail "no Python environment found in .venv/" \
  "Create one:" \
  "  python3 -m venv .venv && .venv/bin/pip install -r api/requirements.txt"

if ! mkdir -p data/artifacts data/logs 2>/dev/null || [ ! -w data ]; then
  fail "data/ is not writable by $(id -un)" \
    "Docker may have created it as root. Fix it with:" \
    "  sudo chown -R $(id -u):$(id -g) data"
fi

DB_PORT="${BEVRO_DB_PORT:-6142}"
# The stack publishes its ports on BEVRO_BIND, which is not always localhost.
# A wildcard bind is reachable here as 127.0.0.1; a specific address is not.
DB_HOST="${BEVRO_BIND:-127.0.0.1}"
case "$DB_HOST" in ""|0.0.0.0|::|"[::]") DB_HOST=127.0.0.1 ;; esac
export BEVRO_DATABASE_URL="${BEVRO_WORKER_DATABASE_URL:-postgresql+psycopg://bevro:bevro@${DB_HOST}:${DB_PORT}/bevro}"
export BEVRO_ARTIFACT_DIR="${BEVRO_WORKER_ARTIFACT_DIR:-$ROOT/data/artifacts}"
export BEVRO_LOG_DIR="${BEVRO_WORKER_LOG_DIR:-$ROOT/data/logs}"
export BEVRO_WORKSPACES_FILE="${BEVRO_WORKER_WORKSPACES_FILE:-$ROOT/config/workspaces.json}"
export BEVRO_SECRET_KEY="${BEVRO_SECRET_KEY:-}"
export BEVRO_SECRET_KEY_FILE="${BEVRO_WORKER_SECRET_KEY_FILE:-$ROOT/data/secret.key}"
export BEVRO_INTEGRATIONS_DIR="${BEVRO_WORKER_INTEGRATIONS_DIR:-$ROOT/data/integrations}"
export BEVRO_AGENTS_DIR="${BEVRO_WORKER_AGENTS_DIR:-$ROOT/data/agents}"
# Folders Connect may inspect and run agents in. Empty means local Connect is off.
export BEVRO_LOCAL_ROOTS="${BEVRO_LOCAL_ROOTS:-}"
export PYTHONPATH="$ROOT:$ROOT/api"

# Is the database there? Failing here is much clearer than a stack trace later.
.venv/bin/python - "$DB_HOST" "$DB_PORT" <<'PY' || fail "can't reach PostgreSQL at ${DB_HOST}:${DB_PORT}" \
  "Start the stack first:" \
  "  docker compose up -d"
import socket, sys
try:
    socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3).close()
except OSError:
    raise SystemExit(1)
PY

# What this worker will and will not be able to do, in one short block. No
# values are printed: only whether something is set.
echo "Bevro worker"
echo "  database      ${DB_HOST}:${DB_PORT}"
echo "  writes to     data/artifacts, data/logs"
if [ -f "$BEVRO_WORKSPACES_FILE" ]; then
  echo "  workspaces    $BEVRO_WORKSPACES_FILE"
else
  echo "  workspaces    none configured - coding agents will have nowhere to work"
  echo "                (copy config/workspaces.example.json to config/workspaces.json)"
fi
if [ -n "$BEVRO_LOCAL_ROOTS" ]; then
  echo "  local roots   $BEVRO_LOCAL_ROOTS"
else
  echo "  local roots   none - local Connect is off (set BEVRO_LOCAL_ROOTS in .env)"
fi
if command -v claude >/dev/null 2>&1; then
  echo "  claude CLI    found"
else
  echo "  claude CLI    not found - Claude Code will show as unavailable"
fi
echo "  Ctrl-C to stop."
echo ""

# Never let the worker look like a nested Claude Code session to the CLI it starts.
unset CLAUDECODE
for v in $(env | grep -o '^CLAUDE_[A-Z_]*'); do unset "$v"; done

exec .venv/bin/python -m app.worker
