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

Then, for `~/agents/market-research`:

```
Found
Market Research & Strategy Agent
Researches changes in the software market.

Capabilities found:
  Research · Market analysis · Product strategy · Competitor analysis · Monitor

Runs via: Runs from this project · Runs on this machine

Authentication required
  OpenAI credential  [ ••••••••• ]

▸ How Bevro found this
[ Connect ]  [ Test connection ]
```

Connect means *add this to my hub*, not *make this callable or fail*. Something
Bevro can send work to is added as that; something with its own app and
nothing for programs is added as *available through its own app*, with direct
access as the quiet alternative ([HUB.md](HUB.md)). Nothing callable is
invented for it. After adding: *Added to Bevro.* It appears under **Apps &
agents** with name, purpose, how it is used, one primary action (**Use in
Bevro**, **Open *name***, **Add credential** …), and its own page: what it
can do, how you can use it, direct Bevro access, then settings (test, look
again, pause, remove, and Advanced details - never the adapter configuration).

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
| a folder (`~/agents/x`, `/srv/x`), or what one is called (`x`, `Market Research`) | `LocalProjectStrategy` → Python, Node, Docker and executable-script inspectors, plus host probes | see below | name, description, entry point, how the request goes in, which key it needs, capabilities from name/description/README |
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

Published ports are read the way Compose reads them, including variables:
`127.0.0.1:${WEB_PORT:-8080}:80` is port 8080, or whatever the project's
`.env` sets `WEB_PORT` to. The `.env` is asked about that one variable and
answers with a port number or nothing; no other value in it is read into
Bevro. A port that cannot be known (a variable with no value and no default,
a range, Docker's own choice) is left out rather than guessed.

### A website is not a way in

Many Compose applications publish one port, for their website, and keep the
service that does the work on their private network, behind a path the
website's web server passes on (`/api/` → the backend). So when what answers
on a published port is a page for people, Bevro:

1. keeps it as evidence - the application is running - and never as a way to
   send work: it is not a runtime, is not ranked, and never makes a provider
   Ready;
2. reads the web server's own configuration for the paths it passes on to
   another service **of the same application** - the files the service's
   Dockerfile copies into `/etc/nginx/` or `/etc/caddy/`, files mounted there,
   or failing those `nginx.conf` / `Caddyfile` in its build directory. Only
   simple forms: nginx `location /x/ { proxy_pass http://service:port; }`
   (also `^~`, `=`, and a one-server `upstream`), Caddy
   `reverse_proxy /x/* service:port` and `handle`/`handle_path` blocks.
   Regular-expression locations and variables are skipped;
3. reads each such path exactly as an address is read (description,
   health), under the website's own published address - so the service
   behind it never has to be reachable directly. A description found there
   is the way in; when the proxy strips the path, operations are called
   under it.

If nothing behind the website describes itself, Connect says so plainly -
*"It's running on this machine. It only has its own website so far: nothing
another program can send work to."* - with **Set up how to use it** as the
next step. Advanced details list what was checked: the website, the paths
behind it and whether they answer, and that no API description was found.

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
| `invocation_label` | how Bevro will talk to it, in words without absolute paths (`python -m market_research.agent --topic "…"`) |
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
  "argv": ["/home/me/agents/market-research/.venv/bin/python", "-m", "market_research.agent"],
  "cwd": "/home/me/agents/market-research",
  "input": {"mode": "flag", "flag": "--topic"},
  "output": {"mode": "stdout"},
  "env": {"PYTHONPATH": "/home/me/agents/market-research/src"},
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
{"name": "Event Allocation Demo", "description": "Events and organisation",
 "capabilities": [{"id": "events.allocate", "title": "Allocate participants"}],
 "invoke_path": "/bevro/invoke", "health_path": "/health",
 "auth": {"type": "bearer"}, "app_url": "https://events.example/app"}
```

It is a fast path, never a requirement.

## Two kinds of HTTP service

A web service Bevro connects to is one of two shapes, and they need different
treatment:

**A prompt service** has one operation that takes natural language. Bevro
finds it, and a task is one call.

**An operational service** has no such endpoint. It has typed operations —
list the cases, create a job, run it, fetch the report — and a piece of work
is two or three of them in order. Most real systems are this shape.

### Finding the API when you pasted the UI

People paste the page they are looking at. A single-page application answers
*every* path with its HTML shell and a 200, so `/p/someone/openapi.json` can
look like a hit and be a web page. Bevro therefore:

1. keeps the exact address that was typed;
2. looks for a description under that path **and** at the origin;
3. checks the content type *and* the shape of the document — a description
   has to declare `openapi`/`swagger` and carry `paths`, or it is not one.

The same rule applies to health: a web page answering 200 at `/health` is a
website being a website, and is not recorded as a health check.

### The operation catalogue

The description is compiled once, at discovery, into a compact catalogue kept
with the runtime: operation id, method, path, summary, tags, parameters, a
flattened request body, what comes back, and a safety class. A real
descriptor can be hundreds of kilobytes; the catalogue is a fraction of that,
and the original is never read again.

### Capabilities, not endpoints

Sixty-eight operations do not become sixty-eight capabilities. Operations are
grouped by the service's own tags and described in the service's own words,
so Agents shows a handful of things a person would recognise. Operation ids,
verbs and schemas stay server-side.

Each capability also keeps the service's *other* words for the same thing,
taken from its own addresses: a tag may say `shortlist` where every URL says
`/opportunities`. A person may ask with either, and both are the service's
own vocabulary rather than Bevro's guess. Those words are used for matching
only - they are not shown, and not sent to a routing model, which can find
synonyms by itself.

### What the service says, and what a person reads

A service that describes itself does so for whoever will integrate with it:
"Thin control API over the X modules. GET routes read. POST routes are
explicit operations." True, useful, and the wrong thing to put on a card.

So the two are kept apart, and neither is lost:

    source_description   what the thing said about itself, stored whole and
                         shown under Advanced details
    description          one short sentence for the card, built by
                         app/connect/copy.py from the name, the capabilities
                         and the mechanism - never from the prose
    details              what it does, how it connects, in plain English

`copy.is_fit_to_show()` is the line between them: length, sentence count and
vocabulary. Text that mentions endpoints, schemas or HTTP verbs is an
integration document, however true it is, and goes to `source_description`.
Copy someone wrote - a built-in's own line, an agent Bevro was asked to
build, a sentence the person typed - passes and is kept as it is.

The sentence itself is built by `app/connect/phrasing.py`, from evidence
rather than from nouns. For each group of operations it asks two questions:

    what is being worked on    the group's tag, unless its own routes use a
                               different word for the same thing - a group
                               tagged `shortlist` whose every path says
                               `/opportunities`
    what is being done to it   the verbs its summaries use, counted, with a
                               path word worth half a summary word and an
                               HTTP method worth almost nothing

That gives "review opportunities", "prepare documents", "run searches".
Groups are then ranked by how much of their work changes or produces
anything - reading a list is useful, but it is not why anyone connected the
thing - and the strongest three become a sentence. Two verbs on one subject
read as one clause ("creates and manages projects"), as does one verb across
two subjects ("reviews applications and opportunities").

Groups named after the machinery (`profiles`, `workspace`, `health`) are
ranked below the rest and left out of the sentence. They still appear under
"Can", where completeness is the point.

A name that ends by describing its own interface is trimmed the same way:
"Inventory REST API" is Inventory, "Acme Search Service" keeps its Service,
and the exact name is kept in `source_name` for Advanced details.

Generation is deterministic, so it works with no model configured. Where
discovery assistance is switched on the model writes the sentence instead,
and the same fitness rule applies to what it returns. The fallback ladder is:
something someone wrote, then the sentence built from operations, then the
plain list of what it works with, then "Connected service" - never the
integration document.

### Scope: which profile, workspace or tenant

Operational systems are often scoped. A pasted route may carry that — but a
guess from a URL is only a candidate. Bevro takes the segments of the entered
path, finds the service's own listing of whatever its operations are scoped
by (`{profile_id}` → `GET /profiles`), and keeps the candidate **only if the
service lists it**. If exactly one exists it is used; if several exist and
the route said nothing, Bevro asks:

```
I found 2. Which should this connection use?
  ( ) Alex Morgan
  (•) Sam Lee
```

Nothing here knows what `/p/` means, or what a profile is.

The same question is asked wherever the API is found: connected by its
address, or found running behind a project folder. The answer is kept
(`source.connection_context`) and applied again by **Look again**. An item
that has never been asked - connected before its API was found - says
"Direct use in Bevro needs you to say which one it's for" and asks on its own
page; it never asks for a key its command line would need instead.

### Safety, from the service's own words

Each operation is classified `read_only`, `work_execution`, `state_change`,
`external_action` or `unknown` — from what the service *says*, not from its
method. A POST the description calls "read-only" is read-only. An operation
that *records* that an external action happened is a record, not Bevro going
and doing it. Only something that actually reaches out — "sends a message",
"external action" — needs approval.

A request permits reading always, running work when it asks for work, and
changing state when it says so. Contacting anyone outside is never planned.

### Planning

A task becomes an `OperationPlan`: a few steps, each naming an operation, its
arguments, and what to carry forward. Where a routing model is configured it
*proposes* one, shown only a short shortlist of relevant operations. Without
a model, one read is matched by its words, and a create → run → read chain is
worked out from the shape of the paths alone.

Whatever the source, Bevro validates every step against the catalogue before
anything is called: the operation must exist, arguments must fit the schema,
enums must hold, and the safety class must be one this request allowed. A
plan cannot invent a URL, a method or a host.

The whole plan is **one** ProviderRun. Results become ordinary artifacts — a
table, a report, a few fields — plus a link back into the service.

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

## Addresses the container cannot reach

A web service can be running perfectly and still be invisible from inside
Docker: a Tailscale address, a VPN, a host-only interface, a firewall that
does not let the Docker bridge back in. That is a fact about the process that
looked, not about the service, and Connect treats it that way.

1. The API asks once whether anything answers at all. It does not walk a list
   of paths against a host that is not going to answer any of them.
2. If nothing answers and a worker is running on the machine, the draft is
   left pending and the worker looks. To the person it is one *Looking…*.
3. Whoever found it is recorded on the runtime, along with whoever tried and
   could not, so the first run does not repeat a wait already known to end in
   nothing.

The draft then says **Runs via: Connected over the network · On this
machine**, because that connection needs `./scripts/worker.sh` to be running
to work at all.

**Test connection** and **Reconnect** follow the same rule: both run wherever
the service can actually be reached. A reconnect the API cannot do is handed
to the worker and waited for, and it refreshes what the service says it can
do as well as how it is reached.

## Asking once, about one thing

Bevro used to decide what it could look at from an environment variable that
had to list every folder in advance, and a restart to change. That is a
reasonable ceiling for someone hardening a shared installation and a poor way
to add your own project, so the question moved to the moment of connecting.

Paste a path. The worker - the process that can actually see the filesystem -
resolves it, and if nobody has agreed to it yet the draft comes back as
`trust_required` with what a person needs to decide:

```json
{"kind": "folder", "label": "alpha", "path": "/home/someone/projects/alpha",
 "parent_label": "projects", "exists": true, "is_directory": true}
```

The browser asks:

> **This is a project on this machine.**
> Allow Bevro to look inside this folder and work in it?
> `/home/someone/projects/alpha`
> [Allow] [Cancel]  ·  Allow everything in projects instead

The path shown is the *resolved* one, with symlinks followed, so what is
agreed to is what will be used. Saying yes writes a `TrustGrant` and the same
draft carries straight on - no second question, no restart.

Three rules hold it up:

* **narrow by default** - a folder, not its parent. A parent is a separate,
  deliberate choice.
* **canonical** - symlinks are resolved before a grant is written, so a link
  added afterwards grants nothing it points at.
* **checked at use** - the worker reads the grants before it looks at or runs
  anything, which is what makes *Take back* in Settings take effect on the
  next use rather than the next restart.

Nothing is granted inside the machine's own folders (`/etc`, `/proc`, `/usr`
...), inside the places credentials live (`~/.ssh`, `~/.aws` ...), or at a
root broad enough to mean the whole machine.

A command is the same question about a program: *"This runs software on this
machine. Allow Bevro to use python3?"* The program is named; its arguments
never are, because an argument may be a secret.

### An address is never asked about

Permission is about this machine, and nothing on the network is on this
machine. A loopback address, a LAN address, a private overlay address and a
public HTTPS address all go through one flow and none of them asks anything.
Which of Bevro's processes can reach them is settled by trying, not by asking
(`docs/RUNTIMES.md`).

Bevro does work out what kind of address it was - `local_machine`,
`private_network`, `vpn_overlay`, `public_network` - but only so that
Advanced details can say so. Nothing behaves differently because of it.

### BEVRO_LOCAL_ROOTS, now

Still there, and now a ceiling rather than a list:

* **unset** (the ordinary self-hosted case): what the person agrees to in the
  browser is the whole policy.
* **set**: the folders named are allowed without anyone being asked, *and*
  nothing outside them can be granted by anyone. A grant made before the
  boundary was set stops counting the moment it is.

## Where local things run: the worker and approved roots

The API usually lives in Docker and cannot see your home directory. Anything
local — inspecting a folder, running a command, starting a stdio MCP server —
is done by the **worker** on the host (`./scripts/worker.sh`), the same
process that runs Claude Code. Connect hands the worker a pending draft and
polls; the person just sees *Looking at market-research…*.

The worker only looks inside folders that have been allowed - the ones the
person agreed to while connecting, plus anything an administrator named in
`BEVRO_LOCAL_ROOTS`, plus Bevro's own directories. Nothing has to be listed
in advance.

Paths are resolved with symlinks followed *before* the check, so a link out
of an allowed folder is refused; `..` is refused; relative paths are refused;
a name (`market-research`) is first turned into a folder (see *Connecting
by name*) and then goes through exactly this. The same rule is applied again at run time by the `command` and `mcp`
adapters, so a stored working directory that is no longer allowed never runs.
An API started on the host, where it can see the filesystem, does this step
itself and needs no worker.

If no worker has reported recently, Connect says so at once instead of
waiting.

## Connecting by name

"If it is on this machine, tell Bevro what it is called." `market-research`,
`Market Research` and `market_research` all mean the folder called any of
those. A name is recognised only when nothing more explicit fits: an
address, a path (`/`, `~/`, `./`, `../`), a command with options or a known
launcher always wins. A few plain words whose first word is a program on
the worker's PATH are still a command, exactly as before.

The worker (`app/connect/names.py`) searches **directory names only** - no
file in any folder is opened - in this order, each to a fixed depth:

| Where | Depth |
|---|---|
| folders the person already allowed | 2 |
| folders their connected local projects sit in | 2 |
| the administrator's `BEVRO_LOCAL_ROOTS`, when set (and then nothing else) | 3 |
| this user's home, when there is no administrator boundary | 4 |

Hidden folders, dependency and cache trees (`node_modules`, `venv`,
`site-packages`, `__pycache__`...), anything trust would refuse (the
operating system's folders, credential folders, outside the boundary) and
network mounts are never entered. Links are never followed down; a link
whose name matches is resolved and kept only if it stays inside a search
area. A match inside another match (a project's own package) is the same
project. The search stops after 25,000 entries or 8 seconds - warm, it takes
a tenth of a second - and returns at most five folders.

One folder: the draft's target becomes that canonical path and the ordinary
permission question follows; finding a folder grants nothing. Several:
Connect shows them (name and `~/...` location) and the person picks one by
position - the paths never leave the server. None: *Bevro couldn't find
anything called "x" on this machine*, and a path still works.

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
- **Permission.** Local reads and runs happen only inside folders someone
  agreed to, checked after resolving symlinks and checked again at run time
  rather than trusted from discovery. `BEVRO_LOCAL_ROOTS`, where an
  administrator sets it, is the boundary those agreements cannot leave.
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
| `credential_required` | *X needs an OpenAI credential before it can run.* / *X couldn't use its credential.* | Add credential (opens its page with the field ready) · Try again |
| `provider_unavailable` | *X isn't available right now.* | Test connection · Retry |
| `invocation_failed` | *X started but couldn't finish this task.* | Try again · Go to X |
| `timed_out` | *X took too long and was stopped.* | Try again · Go to X |
| `cancelled` | *This task was stopped before it finished.* | Retry |
| `output_invalid` | *X answered, but Bevro couldn't read the result.* | Try again · Go to X |
| `configuration_problem` | *Bevro couldn't start X with its current connection.* | Go to X |

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
- An application whose API needs a browser sign-in (a session cookie from a
  login form) is not usable by Bevro, and Bevro does not try: it needs a
  credential another program can hold, such as a token.
- Operations that answer as a stream (Server-Sent Events) are not read by
  the operational HTTP runtime yet.
- Reverse-proxy reading covers the simple nginx and Caddy forms above;
  Traefik labels and anything computed at run time are not read.
