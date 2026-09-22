# Bevro architecture

**Bevro is not an agent framework.**

Bevro is the workspace layer *above* agents, applications, workflows, services,
coding agents and other things that can do work. It never runs an agent loop
itself. It takes a request, hands it to a *provider*, and keeps a faithful
record of what was asked, what happened and what came back.

```
UI (web)
  → Bevro API (FastAPI)
      → router                 proposes a provider: deterministic rules, or a routing model
          → validation         Bevro checks the proposal against the registry
      → task service           creates the Task, its ProviderRun(s), drives state
          → provider registry  what providers exist and what they can do
              → runtime layer  the provider's active RuntimeProfile picks the adapter
                  → adapter    Bevro's implementation of that mechanism: http / command / mcp / local / claude_code
                      → provider   the thing that actually does the work
                      → artifacts / results   stored by Bevro, rendered by the UI
```

The router is separate from execution on purpose: it returns a decision and
nothing else. See [ROUTING.md](ROUTING.md).

Providers get into the registry in three ways: shipped manifests, **Connect**
(something that already exists) and **Create** (something new). The last two
converge at discovery, and from there on nothing can tell them apart:

```
Connect:  target → classify → ConnectionDiscoveryService → DiscoveryStrategy → ProviderDraft → confirm → Provider
Create:   intent → AgentSpec → builder → generated project → the same discovery → Provider
```

**Bevro adapts to providers. Providers should not need to adapt to Bevro.**
See [CONNECT.md](CONNECT.md).

## Vocabulary

| Term | Meaning |
|---|---|
| **Provider** | *What* can do work. A row in `providers` with a name, a list of capabilities and its runtimes. No type field: an AI agent, a SaaS app, a workflow, a person and a coding tool are all just providers. |
| **Runtime** | *How this installation* of a provider runs or is reached: a RuntimeProfile (`adapters/runtime.py`) — an API that is already up, a command in a folder, an MCP server, a Compose service, a systemd unit, or a connection Bevro had built for a project that had none. A provider may have several; one is active. Runtime kinds are not provider types. See [RUNTIMES.md](RUNTIMES.md). |
| **Bridge** | A small piece of Bevro-owned code under `data/integrations/<provider-id>/` that adapts a project with no interface to the ordinary runtime contract. Built by a coding provider chosen by capability; the project is never changed. See [BRIDGES.md](BRIDGES.md). |
| **Capability** | A machine-readable declaration of something a provider can do (`{"id": "research", ...}`). Free-form JSON; both routers match on ids. |
| **Router / RoutingDecision** | The part that proposes which provider should take a request, and the structured proposal it returns. Validated by Bevro before any work starts. |
| **Task** | Bevro's record of one request: the original text, a state, a one-line summary. Owned by Bevro. |
| **Automation** | Work Bevro repeats: an instruction, a recurrence in a named timezone, optionally a condition. Each occurrence becomes an ordinary Task. Agents do work; Bevro owns when. See [AUTOMATIONS.md](AUTOMATIONS.md). |
| **Notification** | Something worth telling the person about, separate from the work that produced it: one `NotificationEvent` per occurrence that mattered, and one delivery row per channel it is sent by. A task that succeeded stays succeeded however the telling goes. See [NOTIFICATIONS.md](NOTIFICATIONS.md). |
| **Delivery channel** | One way of reaching a person — in Bevro, email, a webhook — behind one contract (`validate_configuration`, `health`, `deliver`). Automations know channel names, never mail servers. |
| **ProviderRun** | One execution of work for one task, through the best runtime available at the time. Holds state, progress, the outcome, which runtime ran it, every attempt it took, and adapter-specific metadata. A task may have several runs; one run may have several *attempts*. |
| **RuntimeAttempt** | One try through one runtime inside a single run. Fallback between runtimes is internal resilience, never several pieces of work. See [RUNTIMES.md](RUNTIMES.md#resilience-choosing-a-runtime-at-execution-time). |
| **Artifact** | A result attached to a task (and usually a run): note, report, file, table, diff, deep link, or an unknown type shown as "Open result". |
| **Adapter** | Bevro's implementation of one runtime mechanism: `local`, `http`, `mcp`, `command`, `claude_code`. Each maps into the same contract (`health`, `invoke`, `status`, `cancel`, `collect_artifacts`). Lives in `adapters/`, imports nothing from the API. |
| **ProviderDraft** | What Connect's discovery found, before the person confirms it. Server-side in `connect_drafts`; the browser sees a safe view (no adapter block, no paths). |
| **Local roots** | `BEVRO_LOCAL_ROOTS`: the only folders the host may inspect or run things in for Connect. Checked after resolving symlinks, at discovery and again at run time. |
| **Workspace** | An approved directory a coding provider may work in, with permissions. Configured server-side; the browser sees names, never paths. |
| **Worker** | An optional host process that executes `background` runs (today: Claude Code), reporting progress, heartbeats and availability to PostgreSQL. |

## The parts

| Layer | Where | Responsibility |
|---|---|---|
| UI | `web/` | React + Vite + Tailwind on the supplied Bevro tokens. Home, Recent, Agents, Create, Connect, Settings, Cmd/Ctrl+K. |
| API | `api/app/routers/` | HTTP surface. Serialises only what the browser may see (`schemas/serialise.py`). |
| Runtime layer | `api/app/services/runtime.py`, `adapters/runtime.py` | ProviderRun talks to RuntimeProfiles here: `eligible_runtimes()` ranks and filters them at execution time, `execute()` attempts them in order (`invoke()` + `collect_artifacts()`) and falls back when a runtime - not the work - was at fault, `check_all_runtimes()` restores health. The only place that goes from a provider to an adapter; nothing else branches on a mechanism. See [RUNTIMES.md](RUNTIMES.md). |
| Task service | `api/app/services/tasks.py` | `submit()` creates a Task and its first ProviderRun (and asks for a workspace if one is needed); `begin_run()`/`finish_run()` bracket an adapter call; `execute_run()` does both for quick providers. |
| Scheduler | `api/app/scheduler.py`, `api/app/automations/`, `api/app/services/automations.py` | The clock: claims automations that are due (PostgreSQL decides who gets each), creates ordinary Tasks, decides whether a monitoring result is worth surfacing, and sends whatever is waiting to go out. Its own small Compose service; no queue system. See [AUTOMATIONS.md](AUTOMATIONS.md). |
| Delivery | `api/app/delivery/`, `api/app/services/notifications.py` | Raises one event per occurrence that mattered, queues a delivery per chosen channel, claims and attempts them with modest backoff, and scrubs every payload before it leaves. Knows nothing about automations; automations know nothing about mail servers. See [NOTIFICATIONS.md](NOTIFICATIONS.md). |
| Worker | `api/app/worker.py`, `scripts/worker.sh` | Host process that takes `execution = "background"` runs from PostgreSQL (Claude Code, connected commands, stdio MCP servers) and drives them with progress, heartbeats and cancellation; also discovers and tests Connect drafts that point at local folders. See CODING_PROVIDER.md and CONNECT.md. |
| Workspaces | `api/app/services/workspaces.py`, `config/workspaces.json` | Approved directories coding providers may use. Names to the browser; paths stay server-side. |
| State machine | `api/app/domain/task_state.py` | The eleven task states from the brief and the table of legal transitions. Everything asks this table; nothing compares strings by hand. |
| Provider registry | `api/app/services/providers.py` | Registration, lookup, seeding of the example providers, health checks, "has a worker reported lately". |
| Create | `api/app/create/`, `api/app/services/agents.py`, `api/app/routers/create.py` | Turns one sentence into an AgentSpec (a small model, or local rules), hands the building to whichever provider declares the capability, scans and tests what comes back, then runs *ordinary discovery* over it and registers the result. Versions and rebuilds live in `agent_builds`. See [CREATE.md](CREATE.md). |
| Bridges | `api/app/connect/bridge.py`, `api/app/services/bridges.py` | Decides when a project needs a connection of Bevro's own, describes it (BridgeSpec), hands the work to whichever provider declares the capability, validates what comes back, proves the project is untouched, and registers the result as an ordinary runtime. See [BRIDGES.md](BRIDGES.md). |
| Connect | `api/app/connect/`, `api/app/services/connect.py`, `api/app/routers/connect.py` | Target classification, discovery strategies (URL/OpenAPI, MCP, local Python/Node/Docker projects, commands), `ProviderDraft`, optional model assist, drafts → providers. Local targets are handed to the worker. See CONNECT.md. |
| Router | `api/app/routing/` | `DeterministicRouter` (capability rules; default; fallback) and `LLMRouter` (a routing model behind the vendor-neutral `RoutingModel` interface). Both return a `RoutingDecision` that `validate.py` checks before anything runs. The task service depends only on the `Router` protocol. |
| Secrets | `api/app/services/secrets.py` | Provider credentials, Fernet-encrypted in their own table. Decrypted only inside the API at invocation time. Never serialised. |
| Artifacts | `api/app/services/artifacts.py` | Turns an adapter's `ArtifactDraft` into a stored `Artifact`. Files go to the filesystem under `BEVRO_ARTIFACT_DIR`; payloads and links stay in the database. |
| Adapters | `adapters/` | The boundary. Depends on nothing inside the API so the contract can be read on its own. |
| Providers | `providers/` | Example providers: a JSON manifest each plus a Python handler. |

## Data model

```
Provider ──< ProviderRun >── Task ──< Artifact
    │                                   ▲
    └──< ProviderSecret                 └── (optional) ProviderRun
```

- **Provider** — anything capable of doing work. No provider *type* enum.
  What it can do is a JSON list of capability declarations; how to reach it is
  a JSON `adapter` block (`kind`, `ref`, `config`). Secrets are never in it.
- **Task** — belongs to Bevro. Title, original request, state, summary, timestamps.
- **ProviderRun** — one invocation of one provider for a task. A task can have
  many runs; putting `provider_id` on Task was deliberately avoided. Carries
  `execution` (inline / background), human `progress`, an optional
  `input_request` (a question for the user), `cancel_requested`, worker
  heartbeat fields, and `meta` for adapter-specific execution data.
- **Workspace** — an approved directory with a name, a server-side path and
  permissions (`read`, `write`, `run_commands`).
- **Artifact** — one table for every kind of result. `type` is a free string.
  Text, note, file, report, image, structured data, interactive result, deep
  link and mini-app are the types Bevro can render today; anything else is
  stored just the same and shown as "Open result".
- **ProviderSecret** — encrypted values, one row per named secret.
- **ConnectDraft** — one Connect attempt: what was typed (server-side), the
  full `ProviderDraft`, its state (`pending` for the worker, `found`,
  `failed`, `testing`, `connected`), the last test result.
- **NotificationEvent** — one thing worth telling the person, with its own
  read state. Unique per `automation_run_id`, so one occurrence is announced
  once however many schedulers run.
- **NotificationDelivery** — one event on its way out by one channel, with
  its own status, attempts, next try and plain-words error. Unique per
  (event, channel). Nothing here can change a Task.

## A request, end to end

1. Home posts `{request}` to `POST /api/tasks`. No provider is chosen by the user.
2. `submit()` asks the router for a `RoutingDecision`, validates it (the
   provider exists, is enabled, is available, can be invoked; at most one
   provider), then creates the Task (`created`), a `ProviderRun` (`pending`)
   and moves the task to `queued`. The decision's source, confidence and plan
   are kept on `tasks.routing`. If nothing suitable exists the person is told
   so at once. The response returns immediately.
3. Quick ("inline") providers: a FastAPI background thread runs
   `execute_run()` in its own session. Long ("background") providers such as
   Claude Code: the run stays `pending` and the worker process takes it. Either
   way the run goes `running`, the task `working`, and the UI, polling every
   0.8 s, shows "Working…" and the current phase.
   If the provider needs a workspace and none can be inferred, the task
   pauses in `needs_input` with one question first.
4. The adapter for the provider's `adapter.kind` is looked up and `invoke()`d
   with an `InvocationRequest` (task id, run id, request text, input, secrets).
5. The `InvocationResult` comes back: a state (`completed`, `failed`,
   `needs_input`, `needs_approval`, `running`), a one-line summary, optional
   error, and artifact drafts. Drafts are stored; the run and task states are
   set through the transition table; the summary lands on the task.
6. The UI renders the summary and artifacts. Deep links become
   "Review in Moimio →". The task now appears under Recent.

A provider that raises never takes Bevro down: the run is marked failed with a
plain message, and the internal error goes to the server log only. If the
failure says the *runtime* was unreachable rather than the work impossible,
the run tries the provider's next best runtime before giving up; the person
sees one piece of work either way. A failed
run carries a *failure kind* (credential required, provider unavailable,
execution failed, configuration problem) that the task page turns into a
sentence and actions — Add credential, Retry, Test connection, Manage.
**Retry** (`POST /api/tasks/{id}/retry`) reopens the same task with a fresh
run; it is the one deliberate way out of the terminal `failed` state, and
only a person takes it.

## What is deliberately not here

- No queue system, no Redis, no Celery. Quick runs are one function call in
  a background thread; long runs are taken from PostgreSQL by one plain worker
  process with `SKIP LOCKED`. That is the smallest thing that survives an API
  restart.
- No routing by provider name. Both routers match declared capabilities; a
  routing model is optional, sees only a sanitised catalogue, and its answer
  is a proposal Bevro validates.
- No provider type enum. "AI agent", "workflow", "app", "human", "coding
  agent" are not classes of provider; they are just providers with different
  capabilities and adapters. Claude Code is one provider, and an optional one.
- No raw logs in the UI. `ProviderRun.input` and stack traces never leave the API.
- No Bevro protocol for existing agents. Connect reads what they already
  publish (OpenAPI, MCP, package metadata, a README) and keeps the adapter
  profile on Bevro's side; nothing is written into a connected project. A
  `/.well-known/bevro.json` manifest is optional, never required.
