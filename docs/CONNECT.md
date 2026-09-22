# Connect

**Bevro adapts to providers. Providers should not need to adapt to Bevro.**

Connect means: *I already have something. Let Bevro connect to it.* (Create is
the other door: *I want something new.* The two stay separate.)

The person types one thing — a web address, a folder on the machine, an MCP
server or a command — and Bevro works out the rest. No capabilities to type,
no adapter kind to choose, no schemas. If Bevro is unsure, it says so and
asks for a few plain words. If it cannot work something out at all, there is
an **Advanced setup** page with a handful of technical fields.

```
Connect target
   ↓ classify (text only)
ConnectionDiscoveryService
   ↓ one DiscoveryStrategy: URL / MCP / local project / command
ProviderDraft            server-side; the browser sees a safe view
   ↓ the person confirms (name, plain capability words, a credential if needed)
Provider                 the adapter profile lives on Bevro's side
   ↓
Router catalogue         routable at once; no restart
```

## Zero touch

Discovery is read-only towards the target, always:

- A local project is *looked at*: `pyproject.toml`, `package.json`,
  `requirements.txt`, `README`, `Dockerfile`, `compose.yml`, obvious entry
  files. Nothing is imported, installed, started or written. The project is
  byte-for-byte what it was (a test checks this with a hash of every file).
- A web address is *asked* with `GET`s only: an optional Bevro manifest,
  `/openapi.json`, a health path, the root. No operation that could change
  state is ever called during discovery.
- An MCP server over HTTP receives `initialize` and `tools/list`, the
  protocol's own read-only introduction.
- A command is *parsed*, never run, until a task is assigned to it.
- Credential-looking files (`.env`, `*.pem`, `*.key`, `credentials*`,
  `secrets*`, …) are never opened. Only the variable *names* in
  `.env.example`-style files are read, to know what an agent will need.

When Bevro needs something of its own to make an existing agent usable — a
working directory for a typed command, later perhaps a small wrapper — it
lives under `data/integrations/<provider-id>/`, never inside the target
project.

## What the person sees

```
Connect
Connect an agent, app or service to Bevro.
[ URL, local project, MCP server or command ]
[ Connect ]    Advanced setup
```

Then, for `~/agents/moimio-research`:

```
Found
Moimio Research & Strategy Agent
Researches changes in the church-software market that matter to Moimio.

Capabilities found:
  Research · Market analysis · Product strategy · Competitor analysis · Monitor

Runs via: Runs from this project · Runs on this machine

Authentication required
  OpenAI credential  [ ••••••••• ]

▸ How Bevro found this
[ Connect ]  [ Test connection ]
```

After Connect: *Connected ✓ — available under Agents. Bevro can now route
suitable work here.* The agent appears in **Agents** with name, description,
availability, **Ask**, **Open ↗** when it has an app, and **Manage**
(pause/resume, test, remove, and a short plain-words summary of how it is
connected — never the adapter configuration).

## Provider profile and runtime profile

Discovery answers two different questions and keeps the answers apart:

- the **provider profile** — what can this thing do? (name, description,
  capabilities; what the router sees)
- the **runtime profile(s)** — how is *this installation* actually run or
  reached? (an API that is already up, a command in a folder, an MCP server,
  a Compose service, a systemd unit; what the execution layer uses)

A provider can have several runtimes; discovery ranks them and picks the
most native, safe one, or asks the person when two different mechanisms are
close. All of them are kept: if the preferred one is down when work arrives,
Bevro uses the next and says nothing more than "Recovered using another
connection" ([RUNTIMES.md](RUNTIMES.md#resilience-choosing-a-runtime-at-execution-time)). The person sees only *Runs via: Already running on this machine* and
the like; kinds and mechanisms stay under Advanced details. The whole model,
the ranking rules and the credential strategies are in
[RUNTIMES.md](RUNTIMES.md).

## Discovery strategies

`api/app/connect/strategies/` — one class per kind of target, each with
`supports(target)` and `discover(target, context) -> ProviderDraft`. Adding
a kind of target is one class and one line in `ConnectionDiscoveryService`.

| Target | Strategy | Reads | Infers |
|---|---|---|---|
| `http(s)://…` | `HttpDiscoveryStrategy` | `/.well-known/bevro.json` (optional), `/openapi.json`, `/api/openapi.json`, `/health` & friends, `/` | name, description, the operation that takes a plain request (`POST /ask {"query": …}` and the like), the field that holds the answer, bearer / header-key authentication, capabilities from tags or operations |
| `…/mcp`, `…/sse`, or any address that answers `initialize` | `McpDiscoveryStrategy` | `initialize`, `tools/list` | server identity, one capability per tool, the tool that takes one plain string (and which argument) |
| a folder (`~/agents/x`, `/srv/x`, or a bare name under an approved root) | `LocalProjectStrategy` → Python, Node, Docker and executable-script inspectors, plus host probes | see below | name, description, entry point, how the request goes in, which key it needs, capabilities from name/description/README |
| a command (`python -m my_agent`, `npx my-agent`) | `CommandStrategy` | the text | a name, a request option already present (`--topic`), warnings for unknown programs |

**Python** (static, no import): `[project]` name/description/scripts (also
Poetry), `requirements.txt`, packages under `src/` or the root, the project's
own `.venv`, `__main__.py`, modules with a `__main__` guard and an
`argparse`/`click`/`typer` interface, `python -m …` lines in the README
(these count as documented entry points), `add_argument("--topic")`-style
request options, `FastAPI(`/`Flask(` (a web service to connect by address
once it runs), `FastMCP`/`mcp` imports (a stdio MCP server), and dependency
names that imply a key (`openai`, `anthropic`, …).

**Executable scripts** (any language): files with the executable bit at the
project root and under `bin/`, `scripts/`, `cmd/`. A script the README
documents (`./bin/agent "your question"`) is a high-confidence entry point,
and the usage shown says how the request is passed; a name like `run`,
`start`, `agent` or `cli` is medium; anything else is low. Install, build,
test, lint, deploy and similar helpers are never treated as an interface for
work. A project with no Python or Node metadata at all is connectable this
way, and a wrapper script in a language project is offered next to the
declared module.

**Node**: `package.json` name/description/bin/main/scripts/dependencies,
`@modelcontextprotocol/sdk` (an MCP server), web frameworks (a service to
connect by address), whether `node_modules` exists. `npm install` is never run.

**Docker**: `compose.yml`/`docker-compose.yml` services, published ports,
healthchecks, `depends_on`; `Dockerfile` `EXPOSE`. Infrastructure images
(postgres, redis, …) are skipped. Nothing is started; the draft says *start
it with `docker compose up -d`, then Test connection*, and the test reads the
running service's API description.

## ProviderDraft

`api/app/connect/draft.py`. Discovery never creates a provider directly; it
returns a draft:

| Field | Meaning |
|---|---|
| `name`, `description` | as found or inferred |
| `capabilities` | `[{id, title, description?}]`, conservative, at most five when inferred from text |
| `runtimes`, `active_runtime`, `choice_needed` | every RuntimeProfile found, the chosen one, and whether the person must pick (see [RUNTIMES.md](RUNTIMES.md)) |
| `mechanism`, `mechanism_label` | derived from the active runtime: its adapter family and its plain wording ("Runs from this project") |
| `adapter` | the full adapter block a Provider would get — **server-side only** |
| `invocation_label` | how Bevro will talk to it, in words without absolute paths (`python -m moimio_research.agent --topic "…"`) |
| `availability` | `ready` · `needs_worker` (runs on the host) · `needs_start` (a service that is not running) · `not_invocable` |
| `confidence` | `high` / `medium` / `low`, shown as **Confident** / **Likely** / **Needs review** |
| `evidence`, `warnings` | short sentences, no paths |
| `auth` | `{required, secret_name, label, hint}` — the second field appears only when `required` |
| `invocable` | whether Connect is offered at all; otherwise Advanced setup is |

`public()` is the only view that leaves the server. Drafts are stored in
`connect_drafts` until confirmed (`pending` → `found` / `failed` → `connected`).

## Confidence

- **Confident**: MCP metadata, OpenAPI with a clear asking operation, a
  declared entry point (`[project.scripts]`, `bin`, `__main__.py`, a
  `python -m …` line in the README), a Bevro manifest.
- **Likely**: a module with a command-line interface, a `main` file, strong
  package/README evidence.
- **Needs review**: filenames only, a service that describes nothing, or no
  capability could be inferred. The page then says *I found this provider
  but I'm not fully sure what it can do* and asks for a few plain words,
  which become the capabilities.

## Adapter profiles belong to Bevro

An existing agent keeps its own interface. Bevro stores how to talk to it:

```json
{"kind": "command", "config": {
  "argv": ["/home/me/agents/moimio-research/.venv/bin/python", "-m", "moimio_research.agent"],
  "cwd": "/home/me/agents/moimio-research",
  "input": {"mode": "flag", "flag": "--topic"},
  "output": {"mode": "stdout"},
  "env": {"PYTHONPATH": "/home/me/agents/moimio-research/src"},
  "secret_env": ["OPENAI_API_KEY"],
  "timeout_seconds": 1800}}
```

Transports (all in `adapters/`, see [PROVIDER_MODEL.md](PROVIDER_MODEL.md)):

- **`command`** — a program per task. No shell, ever: the request is one
  argument (`--topic "…"`, the last argument, or standard input). Secrets go
  in as environment variables, never on the command line. What it prints is
  the report; files it names inside its own folder (`Report written to
  reports/x.md`) are attached as artifacts. Runs through the worker.
- **`mcp`** — Streamable HTTP (run by the API) or stdio (run by the worker).
  `tools/call` on the chosen tool; the text content is the answer.
- **`http`** — the Bevro contract (`POST /invoke`) *or* a request profile:
  `invoke: {method, path, body-with-{request}}` and `response: {summary,
  text, link}` dotted paths into the reply.

### The optional Bevro manifest

A service *may* publish `/.well-known/bevro.json` to skip inference:

```json
{"name": "Moimio", "description": "Events and organisation",
 "capabilities": [{"id": "events.allocate", "title": "Allocate participants"}],
 "invoke_path": "/bevro/invoke", "health_path": "/health",
 "auth": {"type": "bearer"}, "app_url": "https://moimio.example/app"}
```

It is a fast path, never a requirement.

## When there is no way in

If a project has useful code but nothing that can take a task — no API, no MCP
server, no usable command, no managed runtime — Connect says so and offers to
have a connection built:

```
This project doesn't expose a connection Bevro can use yet.
[ Make it connectable ]   Advanced setup
```

A coding provider (chosen by capability, never by name) writes a small bridge
into `data/integrations/<provider-id>/`, Bevro validates it and proves the
project is untouched, and the result is an ordinary runtime. If no connected
agent can build one, Bevro says so and offers *Connect a coding agent*. See
[BRIDGES.md](BRIDGES.md).

## Test connection

Offered after discovery, before anything is saved. Always safe:

| Kind | What the test does |
|---|---|
| HTTP | one read-only health/root request |
| MCP over HTTP | `initialize` + `tools/list` |
| local command / project | the worker checks the folder is inside the approved roots and the program is there; nothing runs |
| local MCP server | started once so its tools can be read; the draft's capabilities are refined |
| a service found in a project (`needs_start`) | once you have started it, its address is discovered like a URL and the draft is completed |

No real task runs during a test.

## Where local things run: the worker and approved roots

The API usually lives in Docker and cannot see your home directory. Anything
local — inspecting a folder, running a command, starting a stdio MCP server —
is done by the **worker** on the host (`./scripts/worker.sh`), the same
process that runs Claude Code. Connect hands the worker a pending draft and
polls; the person just sees *Looking at moimio-research…*.

The worker only looks inside **approved roots**:

```sh
# .env
BEVRO_LOCAL_ROOTS=~/agents
```

`:`-separated, `~` allowed. Empty means local Connect is off (addresses and
MCP servers still work). Paths are resolved with symlinks followed *before*
the check, so a link out of a root is refused; `..` is refused; relative
paths are refused; a bare name (`moimio-research`) is looked up directly
under each root. The same rule is applied again at run time by the `command`
and `mcp` adapters, so a stored working directory outside the roots never
runs. An API started on the host with `BEVRO_LOCAL_ROOTS` set discovers
locally itself and needs no worker for that step.

If no worker has reported recently, Connect says so at once instead of
waiting.

## Optional model assistance

`api/app/connect/assist.py`. When routing is in `llm` mode (and
`BEVRO_DISCOVERY_ASSIST` is not `off`), the same routing model may refine a
draft's name, description and capabilities. It receives only a bounded,
sanitised summary: the public draft view plus a README excerpt (≤ 600
characters), dependency and script names, tool or operation descriptions.
Never source files, never `.env` contents, never paths, never credentials.
Its answer is validated as structured output and can only replace the
capabilities (≤ 5), description and name. Discovery works identically
without it; a model failure leaves the deterministic draft in place.

## Advanced setup

`/connect/advanced` → `POST /api/providers`. Roughly seven fields:
connection type (API / MCP server / Command), name, address or command,
working directory, how the request is passed (`--topic`, `stdin`, or the last
argument) or the tool to call, request template and response field (API),
a token or a named secret environment variable, and plain capability words.
It is the escape hatch, not the normal path.

## Security boundary

- **Inspection and execution are separate.** Discovery reads; only an
  assigned task runs anything; a local MCP server is started only when the
  person clicks Test connection.
- **No shell.** Commands are split without a shell and refused if they
  contain `; | & < > $ ( ) { }` backticks or newlines. The request is always
  one argument. Nothing typed on Home can be interpreted.
- **Roots.** Local reads and runs happen only inside `BEVRO_LOCAL_ROOTS`,
  checked after resolving symlinks, and checked again at run time.
- **Nothing leaves the server that should not.** Drafts are serialised
  through `public()`; the router catalogue carries no adapter block; secrets
  are Fernet-encrypted and never returned; the assist model sees a sanitised
  summary only.
- **Read-only probes.** HTTP discovery uses `GET`; MCP discovery uses
  `initialize`/`tools/list`.

## When a run fails

An adapter that fails says *which kind* of failure it was (`failure` on
`InvocationResult`), and Bevro turns that into a sentence and the right next
step. The technical reason stays in the server log; the browser sees:

| Category | Wording | Actions |
|---|---|---|
| `credential_required` | *X needs an OpenAI credential before it can run.* / *X couldn't use its credential.* | Add credential (opens Manage with the field ready) · Retry |
| `provider_unavailable` | *X isn't available right now.* | Test connection · Retry |
| `invocation_failed` | *X started but couldn't finish this task.* | Retry · Manage provider |
| `timed_out` | *X took too long and was stopped.* | Retry · Manage provider |
| `cancelled` | *This task was stopped before it finished.* | Retry |
| `output_invalid` | *X answered, but Bevro couldn't read the result.* | Retry · Manage provider |
| `configuration_problem` | *Bevro couldn't start X with its current connection.* | Manage connection |

Two rules make this honest rather than guessed:

- **The agent keeps its own credential mechanism.** For each credential a
  profile names (`secret_env`), Bevro looks, in this order, for: a value it
  holds itself (an explicit override the person added under Manage), the
  worker's environment (an agent that expects `OPENAI_API_KEY` in its
  environment finds it there, exactly as when it is run by hand), the
  project's own `.env` when the project loads it with dotenv
  (`self_configured`; Bevro only checks that the *name* is defined, never
  reads the value), and only then reports *Credential required*. Discovery
  says which applies: *Uses its own OpenAI credential* or *already set on
  this machine* instead of asking for one. The worker reports the source it
  sees, so Manage can show *Added · From this machine · Uses its own ·
  Missing* even though the API cannot see the host.
- **Pre-flight.** A `command` or stdio MCP provider with a credential that
  none of those sources provides is not started at all. The run fails in a
  millisecond with *Credential required*, and the agent's own folder stays
  exactly as it was — no half-made run directories.
- **Missing credential wins.** When Bevro serialises a failed run, it checks
  the provider's required credentials against what is stored; if one is
  missing, the failure is reported as *Credential required* whatever the
  process said. A run that failed before this model existed is explained
  correctly too.

A non-zero exit is classified from the last lines of the program's stderr
(server-side only): a credential complaint → `credential_required`, a
missing module or file → `configuration_problem`, anything else →
`invocation_failed`; a run that hits its time limit → `timed_out`.

**Credentials from Manage.** Agents → Manage lists what the connection needs
(*OpenAI credential · Missing/Added*). `PUT /api/providers/{id}/secrets/{NAME}`
stores the value Fernet-encrypted; it is never returned; the worker passes it
to the process as the environment variable of that name at run time.

**Retry.** `POST /api/tasks/{id}/retry` reopens a failed task with the same
request and provider as a fresh run on the same task, so Recent keeps one
entry and the earlier attempt stays on the record. *Failed* remains a
terminal state for agents and timers; only a person's retry moves it back
to *queued*. The task page's **Details** shows provider, failure, time and
run id — never paths, secrets, command lines, output or tracebacks.

## Limitations

- HTTP MCP uses the Streamable HTTP transport. The older SSE transport
  (`GET /sse` + `POST /messages`) is not implemented yet.
- An MCP server whose tools all need structured input can be connected and
  listed, but not asked from a plain request; Advanced setup can pin a tool.
- A bridge calls one public callable with one string; a project whose entry
  point needs structured arguments gets defaults and a note ([BRIDGES.md](BRIDGES.md)).
- Capability inference from text is a small vocabulary. The person can
  always edit the plain summary.
