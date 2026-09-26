# Changelog

Notable changes to Bevro. This project uses [semantic versioning](https://semver.org);
while it is pre-1.0, minor versions may change behaviour.

## Unreleased

- The app-backed example provider is now a **Calendar Demo** ("Move my
  meetings to free up Friday afternoon") instead of the Event Allocation
  Demo. A workspace that still has the old one has it removed, or switched
  off if it has done work.
- README and screenshots describe Bevro with everyday agents.

## 0.1.0

The first public release. Bevro is a self-hostable workspace for getting work
done through agents, apps, workflows and other providers.

**The workspace**

- Ask for something in plain words on Home; Bevro creates a Task, chooses a
  provider, watches it work and keeps the result.
- Recent, with search and state filters; a task page showing an honest state,
  one concise outcome, and the artifacts behind it.
- Results render as notes, reports, files, tables, diffs and deep links into a
  provider's own interface. An unrecognised type is still stored and offered.
- A task can ask you a question mid-flight, be cancelled, and be retried.

**Providers, runtimes and routing**

- One provider model for anything that does work — an AI agent, a SaaS app, a
  workflow, a local service, a coding tool. No provider *type*; providers
  declare capabilities.
- Runtimes are discovered per installation: HTTP services, MCP over Streamable
  HTTP and stdio, commands, Python and Node entry points, Docker Compose
  services and systemd units.
- A runtime is chosen at execution time and ranked by health, with quiet
  fallback to another way of reaching the same provider and escalating
  cooldowns when one is down.
- Routing by local rules out of the box, calling nothing. Optionally a small
  model chooses by declared capability; Bevro validates every proposal before
  anything runs, and falls back to the rules.

**Connect and Create**

- Type a URL, a folder, an MCP endpoint or a command. Bevro discovers the rest
  and never modifies the project it is inspecting.
- Where a credential already exists in a provider's own environment, Bevro
  uses it in place rather than asking you to copy it.
- For a project with no interface at all, a coding agent can write a small
  bridge that belongs to Bevro, validated before it is registered, with the
  source project proven byte-for-byte unchanged.
- Create turns a sentence into a real, self-contained agent project, which
  then goes through ordinary discovery. Rebuilds are atomic.

**Scheduling and monitoring**

- Repeat any work on a timetable described in plain words — no cron.
  Timezone-aware, DST-correct, and missed work runs once rather than once per
  missed interval.
- Monitoring stays quiet until a condition is met: the result changed, a
  keyword appeared, a threshold was crossed, or a model's judgement.
- A dedicated scheduler process, with occurrences claimed in PostgreSQL so
  more than one may run safely.

**Notifications**

- A result that matters raises one notification: unread in Bevro, with a
  "Needs your attention" line on Home.
- Optional email (SMTP) and webhook delivery, with modest retries, a
  hard attempt cap, and no retry at all for permanent failures.
- Webhook posts carry a small, stable payload and an optional HMAC-SHA256
  signature.
- Delivery is separate from the work: a task that succeeded stays succeeded
  however the telling goes, and a message is retried rather than the agent
  re-run.

**Running it**

- `docker compose up -d --build` and nothing else. Ports are published on
  `127.0.0.1` only.
- An optional host worker adds local projects and coding agents.
- Provider credentials encrypted at rest; the browser never sees paths,
  adapter configuration, secrets, raw output or delivery addresses.

**Known limitations**

- No authentication and no multi-user model.
- One recipient per installation unless an automation names its own.
- In-app notifications are polled while a browser is open; there is no push.
- Execution runs one provider per task, though the routing schema allows more.
- Coding agents are guard-railed, not sandboxed. See
  [SECURITY.md](SECURITY.md).
