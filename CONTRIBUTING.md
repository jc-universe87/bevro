# Contributing to Bevro

Thanks for looking. Bevro is small and early, which makes contributions easy
to place and easy to review.

## Setup

```sh
git clone https://github.com/jc-universe87/bevro.git && cd bevro
docker compose up -d --build        # web :6140, api :6141, db :6142, scheduler
```

`api/`, `adapters/`, `providers/` and `web/` are bind-mounted; the API and
the web dev server reload on save. After changing `web/package.json`, rebuild
the web image (`docker compose up -d --build web`). Details, environment
variables and the optional coding-provider worker: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Where things are

| | |
|---|---|
| `api/app/routers/` | The HTTP surface. Thin: it validates, calls a service, serialises. |
| `api/app/services/` | What Bevro actually does: tasks, providers, runtimes, automations, notifications. |
| `api/app/models/` | SQLAlchemy models. Every change needs a migration. |
| `api/app/schemas/` | What the browser may see. Allow-lists, never the model. |
| `api/app/domain/` | The state machines. |
| `api/app/connect/`, `api/app/create/` | Discovery, and building an agent from a sentence. |
| `api/app/automations/`, `api/app/scheduler.py` | When work happens. |
| `api/app/delivery/` | How a person is told: in-app, email, webhook. |
| `adapters/` | The provider boundary. Imports nothing from the API, on purpose. |
| `providers/` | The shipped example providers: a manifest and a function each. |
| `web/src/pages/` | One file per screen. `web/src/lib/api.ts` is the only place that calls the API. |
| `docs/` | Written for someone who has never seen the project. |

## Running the checks

```sh
docker compose exec api sh -c 'cd /srv/api && pytest -q'
docker compose exec web sh -c 'npx tsc --noEmit && npx vitest run && npm run build'
```

Both must pass before a pull request. CI runs the same things.

## Migrations

Models live in `api/app/models/`; every schema change needs an Alembic
revision in `api/alembic/versions/` (numbered, hand-written, with a
`downgrade`). Check it applies to a clean database:

```sh
docker compose exec db psql -U bevro -d postgres -c 'CREATE DATABASE bevro_fresh'
docker compose exec -e BEVRO_DATABASE_URL=postgresql+psycopg://bevro:bevro@db:5432/bevro_fresh api alembic upgrade head
docker compose exec db psql -U bevro -d postgres -c 'DROP DATABASE bevro_fresh'
```

## Conventions

- **Plain language in the interface.** Copy is short, practical and human:
  "Working…", "Done. 1 meeting needs your reply." No jargon, no model names,
  no "AI magic". Errors shown to people are sentences, not exceptions.
- **Nothing raw reaches the browser.** Logs, subprocess output, filesystem
  paths, credentials and adapter configuration stay on the server. API
  response schemas are allow-lists; tests assert what is *not* there.
- **Task belongs to Bevro.** Provider-specific data goes in adapter code and
  `ProviderRun.meta`, never on `Task`. No provider-type enums.
- **Colour comes from the design tokens** (`web/src/styles/bevro-tokens.css`
  via Tailwind names like `bg`, `surface`, `ink`, `accent`). No hex values in
  components. The brand assets in `brand/` are used as supplied, never redrawn.
- Python: type hints, `snake_case`, small modules with a docstring saying
  what the module is for. TypeScript: strict, function components, no state
  libraries.
- Keep dependencies restrained. A new one needs a reason in the PR.

## The three extension points

Each is deliberately small. If adding one requires changing anything else,
that is a design problem worth raising in an issue.

**A provider** — something that does work. A JSON manifest in
`providers/manifests/` and one Python function. See
[docs/BUILDING_A_PROVIDER.md](docs/BUILDING_A_PROVIDER.md).

**A runtime adapter** — a new way of reaching providers (a transport Bevro
does not speak yet). Subclass the adapter base in `adapters/`, implement
`health`, `invoke`, `status`, `cancel` and `collect_artifacts`, and register
it. Everything above the adapter — routing, fallback, artifacts, scheduling —
is already generic. See [docs/RUNTIMES.md](docs/RUNTIMES.md).

**A delivery channel** — a new way of telling someone. A class in
`api/app/delivery/` implementing `validate_configuration`, `health` and
`deliver`, listed in `CHANNEL_CLASSES`. Nothing about automations, tasks or
the browser changes. See [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md).

Ship tests with any of them, and never let the normal test suite call a paid
or external service: use a fixture, a fake or a local stub.

## Documentation

`docs/` is written for someone who has never seen the project. If you change
behaviour, change the document that describes it in the same PR, and add a
line to [CHANGELOG.md](CHANGELOG.md) if it is visible to a user. Keep the
concept vocabulary (Provider, Runtime, Capability, Task, ProviderRun,
Artifact, Adapter, Workspace, Worker, Automation, Notification) consistent
across README and docs — the same word should mean the same thing everywhere.

## Pull requests

- One topic per PR, with a short description of *why*.
- Tests and checks pass; new behaviour has a test.
- No personal paths, hostnames, credentials or screenshots of private data.
- Screenshots for UI changes are welcome (light and dark if colour is involved).

Small fixes do not need an issue first. For anything that changes the product
shape — Home, navigation, the provider model — open an issue to talk it
through before writing code.
