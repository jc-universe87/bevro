# Releasing Bevro

A short, repeatable checklist. Everything here can be done from a clean
checkout with Docker; nothing needs private configuration.

## 1. Version

Bevro's version lives in **one** place:

```
api/app/version.py      __version__ = "0.1.0"
```

The API, `/api/meta` and Settings → About all read it from there. Two other
files carry the same number for their own ecosystems and must be updated to
match:

```sh
grep -n '__version__' api/app/version.py
grep -n '"version"' web/package.json
grep -n 'version-' README.md          # the badge
```

## 2. Tests

```sh
docker compose exec api sh -c 'cd /srv/api && pytest -q -p no:warnings'
docker compose exec web sh -c 'npx tsc --noEmit && npx vitest run'
```

Both must pass with no skips other than the documented environment gates
(`node not installed`, `BEVRO_RUN_CLAUDE_INTEGRATION`, `BEVRO_RUN_ROUTER_INTEGRATION`).
No test may make a paid API call.

## 3. Migrations

```sh
docker compose exec api sh -c 'cd /srv/api && alembic upgrade head'
docker compose exec db psql -U bevro -d bevro -c 'select version_num from alembic_version'
```

And from nothing, on a throwaway database, to prove a first install works:

```sh
docker compose exec db psql -U bevro -d postgres -c 'create database bevro_fresh'
docker compose exec api sh -c 'cd /srv/api && BEVRO_DATABASE_URL=postgresql+psycopg://bevro:bevro@db:5432/bevro_fresh alembic upgrade head'
docker compose exec db psql -U bevro -d postgres -c 'drop database bevro_fresh'
```

## 4. Production build

```sh
docker compose exec web sh -c 'npm run build'
```

Check the reported bundle size has not jumped without a reason.

## 5. Fresh clone

Build a copy from only what git would publish, on its own ports, and follow
the README exactly:

```sh
mkdir /tmp/bevro-fresh
git ls-files -c -o --exclude-standard -z | grep -zv '^\.env$' | tar --null -T - -cf - | tar -xf - -C /tmp/bevro-fresh
cd /tmp/bevro-fresh
COMPOSE_PROJECT_NAME=bevrofresh BEVRO_WEB_PORT=6150 BEVRO_API_PORT=6151 BEVRO_DB_PORT=6152 docker compose up -d --build
```

Then, in a browser: Home answers, a task completes, Recent lists it, Agents
shows the built-in providers, Connect discovers a URL, Create shows a preview,
Scheduled and Notifications load, and the scheduler container is running. No
`.env`, no worker, no accounts.

Tear it down with `docker compose down -v`.

## 6. Secret scan

```sh
git ls-files | xargs grep -nIE '(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN)'
git ls-files | xargs grep -nIE '/home/[a-z]+|/Users/[a-z]+'
git ls-files | xargs grep -nIoE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' | sort -u
```

Every hit must be a deliberate fixture or an `example.com`-style placeholder.
Confirm `.env`, `config/workspaces.json`, `data/` and `*.key` are absent from
`git ls-files`.

## 7. Screenshots

`docs/screenshots/` must show generic demo data only: no private providers, no
filesystem paths, no keys, no localhost error states. Retake against a fresh
stack if the UI has moved.

## 8. Documentation

- README reads correctly from the top for someone who has never seen Bevro.
- Every `docs/*.md` link resolves (`docs/RELEASE.md` included).
- New behaviour is documented in the right file, not only in the changelog.

## 9. Changelog

Add a section for the version. Public capabilities and known limitations —
not implementation history.

## 10. Licence

`LICENSE` is the unmodified Apache-2.0 text, `NOTICE` carries the copyright
line, and `TRADEMARK.md` covers the name and logo.

## 11. CI

The workflow must pass on a clean runner with no Claude, OpenAI, SMTP or
private agents available.

## 12. Git

```sh
git status          # clean
git log --oneline -5
```

## 13. Tag and release notes

```sh
git tag -a v0.1.0 -m "Bevro 0.1.0"
git push origin main --tags
```

Release notes are the changelog section for that version, plus one screenshot
and the three installation modes from the README.
