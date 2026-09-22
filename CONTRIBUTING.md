# Contributing to Bevro

Thanks for looking. Bevro is small and early, which makes contributions easy
to place and easy to review.

## Setup

```sh
git clone <repository-url> bevro && cd bevro
docker compose up -d --build        # web :6140, api :6141, db :6142
```

`api/`, `adapters/`, `providers/` and `web/` are bind-mounted; the API and
the web dev server reload on save. After changing `web/package.json`, rebuild
the web image (`docker compose up -d --build web`). Details, environment
variables and the optional coding-provider worker: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

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
  "Working…", "Done. 7 participants need review." No jargon, no model names,
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

## Adding a provider

Read [docs/BUILDING_A_PROVIDER.md](docs/BUILDING_A_PROVIDER.md). A built-in
provider is a manifest and a function; a new transport is an adapter class.
Ship tests with it, and never let the normal test suite call a paid or
external service.

## Documentation

`docs/` is written for someone who has never seen the project. If you change
behaviour, change the document that describes it in the same PR. Keep the
seven concept definitions (Provider, Capability, Task, ProviderRun, Artifact,
Adapter, Workspace, Worker) consistent across README and docs.

## Pull requests

- One topic per PR, with a short description of *why*.
- Tests and checks pass; new behaviour has a test.
- No personal paths, hostnames, credentials or screenshots of private data.
- Screenshots for UI changes are welcome (light and dark if colour is involved).

Small fixes do not need an issue first. For anything that changes the product
shape — Home, navigation, the provider model — open an issue to talk it
through before writing code.
