# Developing Bevro

## Prerequisites

Docker with Compose v2. Nothing else needs installing on the host.

## Ports

| Service | Host port | Notes |
|---|---|---|
| web | 6140 | Vite dev server, proxies `/api` to the API |
| api | 6141 | FastAPI, `/api/...`, Swagger at `/docs` |
| db  | 6142 | PostgreSQL 16, user/password/db `bevro` |

## Run

```sh
git clone <repository-url> bevro
cd bevro
docker compose up -d --build
```

Then open <http://localhost:6140>.

That is the whole core installation: web app, API, PostgreSQL and the
no agents of its own. Set `BEVRO_DEMO_MODE=true` for the example
providers (Research and the Calendar Demo). No configuration file is
required; a secret key for provider credentials is generated once into
`data/secret.key`. Copy `.env.example` to `.env` only if you want to change
ports or set your own key.

**Claude Code is optional.** It needs the `claude` CLI installed and signed in
on your machine, a list of project directories it may work in, and a small
worker process running next to the Docker stack. It is not in Apps & agents
until you add it: Connect and Create offer **Add Claude Code**, and removing
it takes it out again for good. Coding requests without it are declined with
a plain message. Enabling it is described in
[CODING_PROVIDER.md](CODING_PROVIDER.md#enabling-claude-code-on-your-machine).

- The API runs `alembic upgrade head` on start. It adds nothing to Apps &
  agents on its own: the example providers only when `BEVRO_DEMO_MODE=true`,
  and optional integrations (Claude Code) only when you choose to add them.
- `api/`, `adapters/` and `providers/` are bind-mounted; the API reloads on save.
- `web/` is bind-mounted; Vite hot-reloads. `node_modules` lives in a named
  volume, so after changing `web/package.json` rebuild: `docker compose up -d --build web`.

Stop with `docker compose down`. Add `-v` to also drop the database and
artifact volumes (a clean slate).

## Tests and checks

Backend (runs against a separate `bevro_test` database, created on demand):

```sh
docker compose exec api sh -c 'cd /srv/api && pytest -q -p no:warnings'
```

Frontend:

```sh
docker compose exec web sh -c 'npx tsc --noEmit'
docker compose exec web sh -c 'npx vitest run'
docker compose exec web sh -c 'npm run build'
```

Production web image (nginx serving the built bundle, `/api` proxied):

```sh
docker build --target prod -t bevro-web:prod ./web
```

The one test that uses the **real Claude Code CLI** is opt-in, runs on the
host (it needs the CLI, a login and the `.venv` from CODING_PROVIDER.md) and
spends a little quota (one two-turn task, roughly $0.10):

```sh
BEVRO_RUN_CLAUDE_INTEGRATION=1 \
BEVRO_TEST_DATABASE_URL=postgresql+psycopg://bevro:bevro@localhost:6142/bevro_test \
PYTHONPATH=.:api .venv/bin/python -m pytest api/tests/test_claude_integration.py -q -s
```

The **real routing model** test is likewise opt-in and needs a key for the
chosen backend (six small requests, well under a cent):

```sh
docker compose exec -e BEVRO_RUN_ROUTER_INTEGRATION=1 -e BEVRO_ROUTER_BACKEND=openai \
  -e BEVRO_ROUTER_API_KEY=... api sh -c 'cd /srv/api && pytest -q -s tests/test_routing_integration.py'
```

Migrations against a clean database:

```sh
docker compose down -v && docker compose up -d --build
docker compose logs api | grep alembic
```

## Configuration

All API settings are environment variables prefixed `BEVRO_` (see
`api/app/config.py`):

| Variable | Default (compose) | Purpose |
|---|---|---|
| `BEVRO_DATABASE_URL` | `postgresql+psycopg://bevro:bevro@db:5432/bevro` | main database |
| `BEVRO_TEST_DATABASE_URL` | `.../bevro_test` | tests; must contain "test" |
| `BEVRO_ARTIFACT_DIR` | `/data/artifacts` | filesystem artifact storage (named volume) |
| `BEVRO_SECRET_KEY` | *(empty)* | Fernet key for provider secrets; empty means "use `BEVRO_SECRET_KEY_FILE`" |
| `BEVRO_SECRET_KEY_FILE` | `/data/secret.key` | where a generated key is kept when none is set |
| `BEVRO_CORS_ORIGINS` | `http://localhost:6140,...` | browser origins allowed |
| `BEVRO_WORKSPACES_FILE` | `/srv/config/workspaces.json` | approved workspaces for coding providers |
| `BEVRO_LOG_DIR` | `/data/logs` | raw run logs (server-side only) |
| `BEVRO_WORKER_STALE_SECONDS` | `90` | when work nobody has heard from (API, scheduler or worker) is presumed lost, and work never started is given up ([TASKS.md](TASKS.md)) |
| `BEVRO_ROUTER_MODE` | `deterministic` | `deterministic` (local rules) or `llm` (routing model) — see [ROUTING.md](ROUTING.md) |
| `BEVRO_ROUTER_BACKEND` | `openai` | `openai` (any OpenAI-compatible server) or `anthropic` |
| `BEVRO_ROUTER_MODEL` / `_API_KEY` / `_BASE_URL` | *(empty)* | model, key and endpoint for `llm` mode |
| `BEVRO_ROUTER_TIMEOUT_SECONDS` | `8` | how long Home waits for a routing answer before the rules take over |
| `BEVRO_LOCAL_ROOTS` | *(empty)* | **optional boundary** for hardened installations (`:`-separated, `~` allowed). Empty - the ordinary case - means folders are allowed one at a time, in the browser, when Bevro asks. Set, it both allows the folders named and stops anything outside them being allowed by anyone. Read by the worker from `.env`; deliberately not passed into the API container. See [CONNECT.md](CONNECT.md) |
| `BEVRO_INTEGRATIONS_DIR` | `/data/integrations` | Bevro-side storage for connected providers that need somewhere to run (never inside their project) |
| `BEVRO_DISCOVERY_ASSIST` | `auto` | in `llm` routing mode, let the routing model refine a discovered agent's description from sanitised evidence; `off` keeps discovery fully local |

The worker reads the same variables with host values (`scripts/worker.sh`
sets `localhost:6142`, `./data/...`, `./config/workspaces.json`).

**Connecting local agents** needs the worker running on the host
(`./scripts/worker.sh`), because the API in Docker cannot see your folders.
Nothing else: paste the path, and Bevro asks once for permission to look at
that folder. `scripts/worker.sh` also fixes `localhost:6142` and `./data/...`
to host values.
Web addresses and MCP servers over HTTP need neither. The Connect tests use
temporary fixture projects under `api/tests/connect_fixtures.py` and never a
private project.

**Workspaces** (directories coding providers may work in) are configured in
`config/workspaces.json`, which is not tracked by git. Start from
`config/workspaces.example.json`. The API re-reads it on every start; if it
is absent, coding providers simply have nowhere to work. Paths are paths on
the machine that runs the worker.

All of them have working defaults. `.env.example` lists the ones you might
want to change (ports, the secret key, worker paths); copy it to `.env`.

**Set your own secret key** for anything beyond local development:

```sh
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

and put it in `.env` as `BEVRO_SECRET_KEY`. The API and the worker must use
the same key. Changing it makes previously stored provider credentials
unreadable. Startup fails with a clear message if the key is malformed.

## Brand assets

`brand/bevro-brand/` is the supplied package, untouched. The web app serves
copies from `web/public/brand/` and `web/public/icon/`, and imports the token
sheet from `web/src/styles/bevro-tokens.css`. After the package changes, run:

```sh
./scripts/sync-brand.sh
```

Never edit the copies; never edit the package.

## Repository layout

```
bevro/
├── web/          React + TypeScript + Vite + Tailwind
├── api/          FastAPI, SQLAlchemy 2, Alembic, tests
├── adapters/     the provider adapter boundary (local, http, mcp, command, claude_code)
├── providers/    example providers: JSON manifests + Python handlers
├── examples/     sample requests for the HTTP contract
├── config/       workspaces.example.json (copy to workspaces.json, which is not tracked)
├── data/         artifacts/, logs/, integrations/, secret.key (created on first run; not tracked)
├── docs/         this folder
├── brand/        supplied brand package (source of truth, untouched)
├── scripts/      sync-brand.sh, worker.sh
└── docker-compose.yml
```

## Useful API calls

```sh
curl -s localhost:6141/api/providers | python3 -m json.tool
curl -s -X POST localhost:6141/api/tasks -H 'content-type: application/json' \
  -d '{"request":"Move my meetings to free up Friday afternoon"}'
curl -s localhost:6141/api/tasks
```
