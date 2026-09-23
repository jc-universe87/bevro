# Security

## Reporting a vulnerability

Please do not open a public issue for anything that could be exploited.
Use GitHub's private vulnerability reporting on this repository ("Security"
→ "Report a vulnerability"). Include what you found, how to reproduce it and
what you think the impact is. You will get an acknowledgement, and a fix or
an explanation.

Things that are fine as public issues: hardening suggestions, questions about
the boundaries below, documentation gaps.

## What Bevro is, security-wise

Bevro is a local-first workspace that hands tasks to providers. In its
default installation it has **no authentication and no multi-user model**.
Anyone who can reach the web app can ask for work, read every result, add
credentials and connect new providers.

The Compose file therefore publishes every port on `127.0.0.1` only. Setting
`BEVRO_BIND=0.0.0.0` makes Bevro reachable from your network; do that only on
a network you trust, and never put it on the internet without an
authenticating proxy in front of it.

**Bevro is not a sandbox.** It is a workspace that starts things and records
what they return. Where a provider can read files or run commands, it does so
with the privileges of the process that runs it. The sections below say
exactly where those edges are.

## Boundaries

### Provider credentials

Secrets entered under Connect are encrypted at rest (Fernet) with
`BEVRO_SECRET_KEY`, or a key generated once into `data/secret.key` when none
is set. They are decrypted only inside the API or worker at invocation time
and never returned by any endpoint. Protect the key file and the database as
you would the credentials themselves.

Bevro prefers **not** to hold a credential at all. When a provider already
gets one from its own environment, a project `.env` or a secret store, Bevro
records only *where it comes from*, never the value — it does not read `.env`
files into its database, return their contents through the API, or send them
to a routing model.

### Local projects and command execution

`BEVRO_LOCAL_ROOTS` is the only list of directories Bevro may look inside or
run things in for Connect. It is empty by default, which turns local Connect
off entirely. A path is checked **after** symlinks are resolved, at discovery
and again at run time, so a link inside an approved root cannot reach outside
it.

Commands are built as argument lists and executed without a shell, so no
prompt or discovered file can inject shell syntax. Discovery is read-only: it
never writes into a project it is inspecting, never runs a project's install
step, and never starts a service it finds.

### Coding providers (Claude Code, and any future one)

This is the sharp end, and it is worth being precise:

- A coding provider runs as the user who starts `scripts/worker.sh`, on that
  machine, inside a directory listed in `config/workspaces.json`. Bevro never
  lets a prompt or the browser choose a path.
- With `write` permission it modifies files in that directory. With
  `run_commands` permission it runs shell commands. **A shell command can in
  principle read or affect anything that user can.** Bevro passes a deny-list
  (`git push`, `sudo`, `rm -rf /`, `git reset --hard`, …) and the tool's own
  restricted mode, which confines file tools to the working directory and
  denies anything that would prompt a human. Those are guard rails, **not a
  sandbox**. Bevro does not claim guarantees the underlying tool does not
  provide.
- Grant `run_commands` only where that is acceptable. `read` + `write` is
  enough for many tasks. Review "View changes" before trusting a result.
  Never point a workspace at your home directory.

### Generated code: bridges and created agents

When a project has no interface of its own, Bevro can ask a coding provider to
write a small **bridge**, and Create can have one write a whole agent. Both
land under Bevro's own `data/` directory — never inside the project being
connected, which is verified byte-for-byte to be unchanged. What comes back is
scanned and test-run before it is registered, and it then runs through exactly
the same runtime contract as anything else, with the same approved-roots and
no-shell rules.

That code was written by a model. Read it before you trust it with anything
that matters; it is ordinary source, in an ordinary folder, for exactly that
reason.

### Routing model (optional)

With `BEVRO_ROUTER_MODE=llm`, every request typed on Home is sent to the
configured model provider together with a sanitised list of your agents
(names, descriptions, capabilities, availability). No secrets, paths,
endpoints or history go with it. The model's answer is only a proposal, which
Bevro validates against its own registry before anything runs — it cannot
execute anything. The default mode, `deterministic`, sends nothing anywhere.

The same model, when configured, may judge a monitoring condition. It is given
the results being compared as **data, never instructions**, and has no
filesystem, network or provider access.

### External integrations

An HTTP or MCP provider you connect receives the text of the tasks routed to
it. Connect only services you trust with that text.

### Notifications and delivery

What leaves Bevro is a short summary and a link back, never artifacts, logs or
raw model output. Payloads are scrubbed of filesystem paths, key-shaped
tokens and `NAME=value` pairs whose name looks like a credential before they
are sent. Where a message went is never returned to the browser.

- **Email** uses ordinary SMTP settings from the environment. Port 465 uses
  TLS from the start; other ports try STARTTLS and, if the server does not
  offer it, send without — which is what a local test server needs and what
  you should not accept from a remote one. Passwords live in the environment
  and are never returned by any endpoint.
- **Webhooks** post to an address you configure. Set
  `BEVRO_NOTIFY_WEBHOOK_SECRET` and every request carries an
  `X-Bevro-Signature` (HMAC-SHA256 over `<timestamp>.<body>`) so the receiver
  can prove the message came from your Bevro and is not a replay. The secret
  never leaves the server.
- **A webhook address is fetched by the server.** Bevro posts wherever it is
  told, including addresses inside your own network. Only someone who can
  already reach the Bevro UI can set one, and that person can already do more
  than this — but if you put Bevro behind a proxy for several people, treat
  the webhook field as server-side request forgery and restrict it.

### What the browser can see

API responses are allow-listed: no filesystem paths, no adapter
configuration, no secrets, no worker metadata, no raw provider output, no
delivery addresses. Raw tool output, subprocess arguments and logs stay in
`data/logs/` on the server and are never served. Tests in `api/tests/` assert
this; if you find a leak, that is a vulnerability — report it.

### Data at rest

Everything Bevro records — tasks, results, artifacts, encrypted credentials —
lives in the PostgreSQL volume and `data/`. There is no remote storage and
nothing is sent anywhere except to the providers you connect and, if you turn
them on, the routing model and delivery channels.

## Supported versions

Pre-1.0: only the current `main` branch receives fixes. 0.1 is an early
release; treat it accordingly.
