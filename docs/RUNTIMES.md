# Runtimes

Four words, used the same way everywhere in Bevro:

| | |
|---|---|
| **Provider** | *what* can do work: a name, a description, capabilities. Anything: an agent, an app, a service, a person. |
| **Runtime** | *how this installation* of the provider runs or is reached: an HTTP API that is already up, a command in a folder, an MCP server, a Compose service, a systemd unit… A provider may have several; one is active. |
| **Adapter** | Bevro's implementation of one runtime *mechanism* (`adapters/http.py`, `command.py`, `mcp.py`, `local.py`, `claude_code.py`). |
| **ProviderRun** | one execution of work, through the provider's active runtime. |

Runtime kinds are **not** provider types. "Research" is a provider; that it
happens to run as a Python entry point today is a runtime; tomorrow it may
run as a service and nothing else about it changes.

**Bevro adapts to providers. Providers should not need to adapt to Bevro.**
Discovery reads what an installation already is; nothing is written into it.

## RuntimeProfile

`adapters/runtime.py` — provider-neutral, importable on its own.

| Field | Meaning |
|---|---|
| `id` | short and stable within the provider (`running`, `compose`, `cli`, `mcp`, …) |
| `kind` | `http`, `mcp_http`, `mcp_stdio`, `cli`, `docker_compose`, `systemd`, `process`, `python_entrypoint`, `node_entrypoint`, `file_exchange`, `builtin`, `unknown` |
| `display_name` | plain wording the person sees: *Already running on this machine*, *Runs as a local service*, *Runs from this project*, *Connected over the network*, *Uses MCP* |
| `confidence` / `availability` | `high` `medium` `low` / `ready` `needs_worker` `needs_start` `not_invocable` |
| `adapter` | invocation metadata: the adapter block Bevro runs. **Server-side only.** |
| `health` | health-check metadata (a path…). Server-side only. |
| `credentials` | the *credential strategy* (below), the secret names it involves, whether one must come from the person |
| `target` | working directory, URL, unit name. Server-side only. |
| `input` / `outputs` | how a request goes in and what comes out (below) |
| `abilities` | what the runtime can do for Bevro, distinct from provider capabilities |
| `evidence` / `warnings` | short sentences, no paths |
| `priority` | the rank the selection rules gave it |
| `health` | what recent runs and checks said (below) |

`public()` is the browser's view (wording, credential label, abilities,
evidence). `advanced()` names kinds and mechanisms for "Advanced details".
Neither carries a path, a command line, a host or a secret.

### Runtime abilities vs provider capabilities

Provider capabilities say *what work* (`research`, `coding`,
`events.allocate`, `reporting`); routers choose by them. Runtime abilities
say *what the mechanism can do for Bevro*: `accepts_prompt`, `background`,
`cancel`, `status`, `streaming`, `file_artifacts`, `health`. The router
never sees the runtime; the execution layer never looks at capabilities.

### Credential strategy

How this runtime normally gets its secrets:

| Strategy | Meaning | Bevro's part |
|---|---|---|
| `runtime_managed` | a service that holds its own (a remote API, a running process) | nothing |
| `inherited_environment` | the worker's environment carries it (the way a person runs it by hand) | pass the environment through |
| `project_dotenv` | the project loads its own `.env` (dotenv) and that file defines the name | nothing — the *name* is checked, the value never read |
| `docker_environment` | Compose `environment` / `env_file` | nothing |
| `systemd_environment` | a unit's `EnvironmentFile` | nothing |
| `external_secret_store` | a vault the runtime talks to | nothing |
| `bevro_managed` | Bevro stores it encrypted and injects it at run time | ask once, inject as an environment variable or header |
| `none` / `unknown` | | |

Bevro prefers the native strategy and asks the person only when the selected
runtime genuinely cannot get the secret otherwise **and** Bevro can inject it
(`required_from_user`). At run time the same precedence is applied by the
adapter: a value Bevro holds (an explicit override) → the worker's
environment → the project's own `.env` → *Credential required*. Bevro never
reads a secret value to learn how a runtime works.

### Input and output normalisation

| Input | Meaning |
|---|---|
| `argument` / `flag` | last argument, or `--topic "…"` |
| `stdin` | on standard input |
| `http_json` | a JSON body field (`{"query": "{request}"}`) |
| `mcp_argument` | a tool argument |
| `file` | written to a file under Bevro's own scratch folder, path passed |
| `environment` | an environment variable |
| `queue` | not run by Bevro yet |

| Output | Becomes |
|---|---|
| `stdout` | a `report` artifact |
| `json_response` | the mapped `text`/`summary`, plus `structured` details |
| `files` | files the program names inside its folder → `report` / `file` artifacts |
| `report_dir` | files that appeared under a directory during the run |
| `http_result` | the Bevro HTTP contract's artifacts |
| `mcp_result` | the tool's text (and `structuredContent`) |
| `deep_link` | a URL → "Open in X" |

Every adapter's `collect_artifacts()` performs this; raw output stays
server-side (a log file), never in the run record.

## The execution contract

```python
class RuntimeAdapter(Protocol):
    kind: str; execution: str; requires: frozenset[str]
    def health(provider, secrets) -> HealthResult
    def invoke(provider, request, context) -> InvocationResult
    def status(provider, external_ref, secrets) -> InvocationResult
    def cancel(provider, external_ref, secrets) -> bool
    def collect_artifacts(provider, result, context) -> list[ArtifactDraft]
```

`api/app/services/runtime.py` is the only place that goes from a provider to
its adapter: `execute()` = `invoke()` + `collect_artifacts()`, `health()`,
`cancel()`, `execution_of()`, `requires_of()`. The task service, the worker,
the catalogue and the validator call it; none of them branches on a kind.
`ADAPTER_FOR_KIND` in `adapters/runtime.py` maps runtime kinds to adapter
kinds and is the whole of "which mechanism drives what".

### Failure model

Every adapter translates its native errors into one of:
`configuration_problem`, `credential_required`, `provider_unavailable`,
`invocation_failed`, `timed_out`, `cancelled`, `output_invalid`. The
interface turns the kind into a sentence and actions; it never needs a
provider-specific error code.

## Discovery and ranking

For a local folder, discovery looks systematically for: processes of yours
listening on a port from that folder; Compose services (answering, or
startable) and their environment; systemd units shipped with the project
(and, read-only, whether systemd says they are active); MCP servers; declared
CLI entry points (`[project.scripts]`, `bin`, `__main__.py`, a `python -m …`
line in the README); executable scripts at the root or under `bin/`,
`scripts/`, `cmd/` in any language, matched against launch commands the
README documents (build and install helpers are skipped); inferred entry
points; web frameworks with nothing running. For a URL: an optional Bevro manifest, OpenAPI, MCP `initialize`,
health, root. Every runtime found is kept on the provider; one is active.

Preference: **already running** → **managed and reachable** (Compose,
systemd) → **declared MCP / CLI** → **inferred entry point** → a Bevro-side
wrapper last. The score (`api/app/connect/runtimes.py`) adds points for
being ready now, confidence, status/cancel/file abilities and a native
credential strategy; it subtracts for needing a start, needing a credential
from the person, or not accepting a task. Two *different* mechanisms within a
few points → the person is asked: *I found two ways to connect this.* A
runtime that cannot take a task (a scheduled systemd job) is recorded and
shown under Advanced details, never selected.

The optional model assist may settle an uncertain choice, but only among the
runtimes discovery found and only from a bounded summary (names, labels,
README excerpt, tool/operation descriptions).

## Resilience: choosing a runtime at execution time

```
Provider  →  ranked RuntimeProfiles  →  RuntimeAttempt(s)  →  one ProviderRun
```

Nothing is fixed in advance. When a run starts, the execution layer takes the
provider's runtimes, ranks them afresh (same rules, now including health), drops
the ones that cannot be used *right now*, and takes the best. If that one turns
out to be down, it moves to the next. One ProviderRun, one piece of work,
whatever it took to get there.

**This is internal resilience, not orchestration.** Several runtimes are
several ways of reaching *the same provider*; Bevro never spreads one piece of
work across providers, and the router is not involved (it chose the provider
and nothing else).

### What makes a runtime eligible

A runtime is passed over when it cannot take a task, has no adapter, needs a
credential Bevro has not been given, needs the host while the run is happening
inside the API, or is resting after recent failures. If *everything* is
ineligible, Bevro still puts the best candidate forward rather than refuse the
work with a vague sentence — a runtime that cannot take a task explains itself
far better than "not available" ever could.

Where a run happens follows from the same facts: a provider with any way in
that needs the host is handed to the worker, which can drive every mechanism
and so can fall back freely. If it also has a way the API can reach and no
worker is about, the API takes it rather than let the work wait.

### When Bevro tries another way

| The failure says | Category | Another way? |
|---|---|---|
| `provider_unavailable`, `configuration_problem` | the runtime was at fault: nothing really started | **yes** |
| `credential_required` | this runtime's secret | only a runtime whose credentials come from somewhere else |
| `timed_out` | unknown: work may have started | only with `BEVRO_RUNTIME_FALLBACK_ON_TIMEOUT=true` |
| `invocation_failed`, `output_invalid` | the provider took the work and it went wrong | no — another way in would do the same |
| `cancelled` | the person stopped it | never |

A runtime is tried at most once per run, and at most `MAX_ATTEMPTS` (4) are
made. When every reachable way has failed but another was held back only for
want of a credential, that is what the person is told: it is the thing they
can act on.

### RuntimeAttempt

Each try is recorded on the run (`meta.attempts`), never shown raw:
`runtime_id`, `kind`, `started_at`, `completed_at`, `outcome`
(`completed` / `failed` / `cancelled` / `skipped`), `failure_kind`,
`fallback_reason`, and `contributed` — whether that attempt's artifacts became
the task's. Only the successful attempt contributes, so a fallback never
duplicates results. `meta.runtime` records which way actually did the work.

### Health and rest periods

Each profile carries `health`: `state` (`available` · `degraded` ·
`unavailable` · `unknown`), `last_checked_at`, `last_failure_at`,
`consecutive_failures`, `cooldown_until`. A runtime-level failure degrades it
and rests it for 1, then 5, then 15 minutes; a success clears everything. One
transient failure never disables a runtime: the rest period is a preference,
and ranking (not a ban) is what keeps work flowing to a healthy way in — a
slightly less preferred runtime that works now beats a preferred one that is
known to be down.

A runtime comes back on its own: **Test** in Manage checks every way this
process can reach, the worker re-checks on its own cycle, a rest period
expires, or Reconnect rediscovers the provider. Nothing has to be deleted or
reconnected.

### What the person sees

Normally nothing new: *Working…*, then *Done.* If a fallback saved the run,
the task says, quietly, "Recovered using another connection." Manage shows
**Preferred**, **Alternatives — N available · used only if this one can't be
reached**, and **Test all connections**; mechanisms stay under Advanced
details. Runtime ids and kinds never appear in the normal interface.

## Agents Bevro created

An agent built by Create gets its runtimes the ordinary way: discovery is run
over the generated project and whatever it finds — usually a command-line
entry point — becomes the RuntimeProfile. There is no "created agent" runtime
kind and no special registration path. See [CREATE.md](CREATE.md).

## When a project has no way in at all

A project may hold useful logic and expose nothing that can take a task. Bevro
can have a small **connection** built for it by a coding provider — code that
belongs to Bevro, lives under `data/integrations/<provider-id>/`, and never
touches the project. It becomes an ordinary RuntimeProfile (a CLI runtime
speaking a small JSON contract) and takes part in health, ranking, fallback,
cancellation and artifacts like any other.

It is always the last resort: **native runtime > managed runtime > generated
bridge**, and the ranking makes that so without a special rule. The whole
mechanism is described in [BRIDGES.md](BRIDGES.md).

## Adding a runtime mechanism

Third parties add a mechanism without touching Task, Provider or the task
service:

1. Write `adapters/<mechanism>.py`: a class extending `BaseRuntimeAdapter`
   with `kind`, `execution` (`inline` or `background`), and the contract
   methods it needs (`check`/`health`, `invoke`, optionally `status`,
   `cancel`, `collect_artifacts`). Translate native errors into
   `FailureKind`. Register it: `register_adapter(MyAdapter())`.
2. Add the `RuntimeKind` and its entry in `ADAPTER_FOR_KIND`.
3. If discovery should find it, add a builder in
   `api/app/connect/runtimes.py` and produce the profile from a strategy or
   inspector. Give it a `display_name` in plain words and a credential
   strategy.

Nothing else changes: ProviderRun already talks to RuntimeProfile.

## Zero touch and Bevro-owned bridges

Discovery, testing and connection never write into the target: no manifests,
no source edits, no installs, no environment rewrites, no dotenv, no Docker or
unit edits. When Bevro needs a place of its own (a typed command's working
directory, a request file for `input: file`, a future bridge), it lives under
`data/integrations/<provider-id>/`, owned by Bevro.

## Migration

Providers created before runtimes existed get one profile derived from
their adapter block at API start (`ensure_runtimes`): built-in example
providers → `builtin`, the coding tool → `cli`, connected APIs → `http`,
commands → `cli`, MCP → `mcp_http`/`mcp_stdio`, Create's declared providers →
`unknown` (not invocable). Names, capabilities, secrets and history are
untouched; `provider.adapter` stays in step with the active runtime's block.
