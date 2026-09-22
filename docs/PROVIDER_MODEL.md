# The provider model

A **provider** is anything capable of doing work. Bevro does not care whether
that is an AI agent, a multi-agent system, a SaaS application, a workflow, a
daemon on your machine, a coding agent or a person. Four words, always used
the same way:

| | |
|---|---|
| **Provider** | what can do work: name, description, `capabilities` |
| **Runtime** | how *this installation* runs or is reached: `runtimes` (a list of RuntimeProfiles) and `active_runtime` |
| **Adapter** | Bevro's implementation of a runtime mechanism (`adapters/`) |
| **ProviderRun** | one execution of work, through the best runtime available then (with attempts recorded if it had to fall back) |

So a provider record carries:

1. **What it can do** — `capabilities`, a JSON list.
2. **How it runs** — `runtimes`, one or more RuntimeProfiles ([RUNTIMES.md](RUNTIMES.md)), each carrying its own health; `adapter` is the preferred one's invocation block, kept in step for queries. Which one actually runs a given piece of work is decided when that work starts.
3. **What it needs kept secret** — stored separately, encrypted, never shown; each runtime says how it normally gets its secrets (its credential strategy).

For a step-by-step guide to adding one, see [BUILDING_A_PROVIDER.md](BUILDING_A_PROVIDER.md).

## The provider record

The example below is the shipped **Moimio demo** — a stand-in for a
specialist application that answers with a summary and a deep link. It uses
sample data and a sample URL; Bevro does not depend on it.

```json
{
  "slug": "moimio",
  "name": "Moimio",
  "description": "Events and organisation (demo)",
  "enabled": true,
  "capabilities": [
    {"id": "events.allocate", "title": "Allocate participants",
     "description": "...", "input": {"type": "object", "properties": {...}}}
  ],
  "adapter": {"kind": "local", "ref": "providers.moimio_example:run", "config": {...}},
  "app_url": "https://moimio.example/app",
  "icon": {"kind": "letter", "text": "M"},
  "origin": "example"
}
```

- `capabilities[]` — each entry needs an `id`; `title`, `description` and a
  JSON-Schema `input` are optional. Nothing validates the ids against a fixed
  list; new capabilities need no code change in Bevro.
- `runtimes[]` / `active_runtime` — how this installation runs; the active
  profile's `adapter` block is mirrored into `adapter` below.
- `adapter.kind` — which adapter (mechanism) drives the active runtime:
  `local`, `http`, `mcp`, `command`, `claude_code`, or `declared` (a provider
  that exists on paper but cannot run yet).
- `source` — what Connect was given, so Manage → Reconnect can look again. Server-side only.
- `adapter.config` — non-secret transport settings. Never put credentials here.
- `app_url` — if present, the UI offers "Open ↗" and providers can build deep links from it.
- `origin` — `example` (shipped), `created` (from Create), `connected` (from Connect).

The browser only ever sees the record without `adapter` (`schemas/providers.py`);
it gets `actions` (`ask`, `open`) and a `connection` label instead.

## The adapter contract

`adapters/base.py` and `adapters/runtime.py` are the whole boundary. An
adapter is Bevro's implementation of one runtime mechanism and maps into one
contract — `health()`, `invoke()`, `status()`, `cancel()`,
`collect_artifacts()` — so that the rest of Bevro never knows whether a
provider runs over HTTP, Docker, systemd, a CLI or MCP
([RUNTIMES.md](RUNTIMES.md#the-execution-contract)). In detail:

```python
class ProviderAdapter(Protocol):
    kind: str
    execution: str = "inline"            # or "background": a worker runs it, it may take minutes
    requires: frozenset[str] = frozenset()   # e.g. {"workspace"}: Bevro supplies it or asks the user
    def check(self, provider: ProviderSpec, secrets: dict[str, str]) -> HealthResult
    def invoke(self, provider: ProviderSpec, request: InvocationRequest, context: InvocationContext | None = None) -> InvocationResult
    def get_status(self, provider, external_ref, secrets) -> InvocationResult   # optional: raise NotSupported
    def cancel(self, provider, external_ref, secrets) -> bool                  # optional: raise NotSupported
```

`InvocationRequest` carries `task_id`, `run_id`, the request text, an `input`
dict (for coding providers: the resolved `workspace` and `permissions`) and
the resolved `secrets` for this call only.

`InvocationContext` is how a long run talks back: `context.progress("Running
checks")` persists a short human phase; `context.cancelled()` tells a
well-behaved adapter to stop; `context.log_dir` is where technical logs may
go (server-side only).

`InvocationResult` carries a `state` (`completed`, `failed`, `needs_input`,
`needs_approval`, `running`, `cancelled`), a short `summary`, an `error`, for
failures a `failure` kind (`configuration_problem`, `credential_required`,
`provider_unavailable`, `invocation_failed`, `timed_out`, `cancelled`,
`output_invalid` — what the person can act on; see
[CONNECT.md](CONNECT.md#when-a-run-fails)), an `external_ref` for later
status/cancel calls, `artifacts: list[ArtifactDraft]` and `metadata`
(adapter-specific execution data, stored on the run, never shown).

`ArtifactDraft` is how a provider proposes a result: `type`, `title`,
`summary`, `mime_type`, `payload` (JSON), `content` (bytes to store),
`filename`, `external_url`, `metadata`. Bevro decides where it lives.

Adapters register themselves: `register_adapter(MyAdapter())` in
`adapters/registry.py` makes a new `kind` available to every provider record.

## Transports today

### `local` — a Python callable in this process
`ref` is `"module:function"`. The function takes `(InvocationRequest,
ProviderSpec)` and returns `InvocationResult`. The two example providers use
this. It is also the simplest way to wrap a self-hosted tool that ships a
Python shim.

### `http` — a service speaking a small JSON contract, or its own
`config.base_url`, optional `invoke_path` (`/invoke`), `health_path`
(`/health`), `status_path`, `cancel_path`, and `auth: {"type": "bearer",
"secret": "api_key"}` (or `{"type": "header", "name": "X-API-Key", "secret":
"api_key"}`) naming which stored secret to send.

A service that speaks its own JSON gets a *request profile* instead, usually
filled in by Connect from its OpenAPI description:

```json
{"base_url": "http://sales.local",
 "invoke":   {"method": "POST", "path": "/ask", "body": {"query": "{request}"}},
 "response": {"summary": "answer", "text": "answer", "link": "url"}}
```

Only `{request}` inside `body` strings is substituted; `response` values are
dotted paths into the reply. The service is not changed to fit Bevro.

```
POST {base_url}/invoke
{"task_id": "...", "run_id": "...", "request": "...", "input": {...}}

200 {"state": "completed", "summary": "Done. ...", "external_ref": null,
     "artifacts": [{"type": "deep_link", "title": "Review in X", "external_url": "..."}]}
```

That is enough for an external SaaS, a workflow engine or a coding agent
behind a thin HTTP shim to be connected from the Connect screen today.

### `mcp` — a Model Context Protocol server
`config.server_url` (Streamable HTTP; run by the API) or `config.argv` +
`config.cwd` (a stdio server; run by the worker on the host, inside the
approved local roots). `config.tool: {"name": "search", "argument": "query"}`
names the tool that takes a plain request; Connect picks it from
`tools/list`, Advanced setup can override it. The client
(`adapters/mcp_client.py`) implements `initialize`, `tools/list` and
`tools/call` and nothing else. The adapter's execution mode depends on the
transport (`execution_for`), so one `kind` serves both.

### `command` — a program Bevro starts per task
The way an existing local agent is used without changing it. `config.argv`
(a list; never a shell string), `cwd` (a server-side folder inside the
approved local roots, or absent for Bevro's own `data/integrations/<id>/`),
`input` (`{"mode": "flag", "flag": "--topic"}`, `argument`, `stdin`, `none`),
`env` (non-secret), `secret_env` (names of stored secrets passed as
environment variables), `timeout_seconds`. Standard output becomes the report;
files the program names inside its own folder are attached. Runs through the
worker; the folder rule is enforced again at run time.

### `claude_code` — the local Claude Code CLI (background)
Runs `claude -p` non-interactively in an approved workspace, on the host,
through the worker. Fully described in [CODING_PROVIDER.md](CODING_PROVIDER.md),
including how a Codex adapter would sit next to it.

### `declared` — from Create
Create stores a provider with `adapter.kind = "declared"` and the spec
(source description, permissions) under `adapter.spec`. It has no `ask`
action. A **builder provider** (Claude Code, Codex, ...) can later read that
spec, build the thing, and replace the adapter block with a real `local` or
`http` reference — without touching Task, ProviderRun or Artifact.

## Deep links

A provider that knows its own application returns an artifact of type
`deep_link` with an `external_url`. The UI renders it as the headline action
("Review in Moimio →"). The example Moimio provider builds the URL from the
provider's `app_url` and a `review_path` pattern in `adapter.config`; a real
Moimio would return the actual URL.

## Providers never own a timetable

A provider is asked for one piece of work at a time. Repeating that work is
Bevro's business: an Automation holds the instruction, the recurrence and any
condition, and each turn becomes an ordinary Task through the ordinary
machinery. No provider — connected, created or built-in — should contain a
loop, a cron entry or a scheduler. See [AUTOMATIONS.md](AUTOMATIONS.md).

## Agents Bevro created

**Create** turns a sentence into an AgentSpec, has a coding provider build a
real, self-contained project under `data/agents/<provider-id>/versions/<build>/`,
and then runs the *same* discovery Connect uses over it. The provider that
results is ordinary in every way; its capabilities come from the spec the
person agreed to, and `origin` is `created`. See [CREATE.md](CREATE.md).

## When a project has no interface

A project with useful code and no way in can be connected through a **bridge**:
a small program Bevro owns, written by a coding provider into
`data/integrations/<provider-id>/`, which imports or calls the project where it
already is and speaks a tiny JSON contract. It registers as an ordinary
`command` runtime and is outranked by any native one. The project itself is
never modified. See [BRIDGES.md](BRIDGES.md).

## Adding a provider without writing code

**Connect** → type an address, a folder, an MCP server or a command. Bevro
discovers the rest (name, description, capabilities, how to reach it, whether
a key is needed) and shows it for confirmation; see [CONNECT.md](CONNECT.md).
**Advanced setup** is the manual form for what discovery cannot work out. A
key is encrypted with the server's `BEVRO_SECRET_KEY` and never returned to
the browser.

## Adding a built-in provider

1. Add `providers/manifests/<slug>.json`.
2. Add `providers/<slug>.py` with `run(request, provider) -> InvocationResult`.
3. Restart the API; `seed_examples()` inserts it if the slug is new.
