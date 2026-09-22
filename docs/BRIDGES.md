# Connection bridges

Some projects hold useful logic and offer no way in: a package with public
functions, a module with exports, and no API, no MCP server, no command. Bevro
can have a small **connection** built for such a project — code that belongs to
Bevro, lives outside the project, and adapts it to the ordinary runtime
contract.

**The external project is never changed.** Bevro adapts to it.

```
external project        untouched, used where it already is
      ↑
Bevro-owned bridge      data/integrations/<provider-id>/bridge.py
      ↑
RuntimeProfile          an ordinary CLI runtime speaking a small JSON contract
      ↑
ProviderRun             one execution of work, like any other
```

Once built, nothing in Bevro knows the connection was generated. It takes part
in health checks, ranking, fallback, cancellation, artifact collection,
availability, Connect, Agents and Recent exactly like any other runtime.

**Order of preference, always:** a native runtime (an API, MCP, a declared
command) beats a managed one (Compose, systemd) beats a connection Bevro built.
A bridge is the last resort, and it is outranked the moment the project grows
an interface of its own.

## When one is offered

Only when discovery finds a project worth connecting, with something callable
in it, and **nothing** that can take a task: no HTTP API, no MCP server, no
usable command, no managed runtime. If any way in exists, that way is used and
no bridge is built. If there is nothing callable either, Bevro says so plainly
rather than inventing something.

What the person sees under Connect:

```
Found
Widget Brain
Answers questions about the widget market

This project doesn't expose a connection Bevro can use yet.

[ Make it connectable ]   Advanced setup
```

Then, while it happens:

```
Preparing connection…
  Inspecting project
  Building connection
  Testing connection
```

and afterwards, *Connected — available under Agents*. No adapter code is
written by the person, and imports, wrappers and JSON schemas are never
mentioned.

## Who builds it

Bevro asks its own provider registry for a **capability**, never a name:
`integration_bridge_building`, or plain `coding` if nothing declares the
former. Whichever available provider offers it is given the work — Claude
Code, Codex, or anything a third party connects later. If nothing can build
one, Bevro says so and offers *Connect a coding agent* and *Advanced setup*;
it never fails quietly.

Building is real work, so it is an ordinary Bevro **Task** with its own
ProviderRun, visible under Recent as "Preparing a connection for X". This is
also the first time Bevro uses one provider to create connectivity to another.

## What the builder is told: the BridgeSpec

A bounded description, assembled by `api/app/connect/bridge.py`:

| | |
|---|---|
| provider name, description | what the thing is |
| project path | the approved folder, on the machine that will run it |
| language / framework | from the project's own metadata |
| evidence | file names, a README excerpt, dependency names, and the **public callables** found by reading the source (module, name, arguments) |
| capabilities | what the provider is expected to be able to do |
| bridge folder | the only place it may write |
| interpreter | the project's own virtual environment, when it has one |
| contract | the input and output shape below |
| restrictions | the rules that matter more than finishing |

Never sent: `.env` contents or any secret, credentials, unrelated
repositories, or free rein over the filesystem. The whole request is a few
thousand characters — a description, not a repository.

## The contract a bridge speaks

Deliberately tiny. Standard input:

```json
{"request": "what the person asked, in plain words", "context": {}}
```

Standard output, exactly one object:

```json
{"status": "completed", "summary": "one plain sentence",
 "artifacts": [{"type": "report", "title": "...", "text": "markdown"}]}
```

Artifacts may be `report`, `structured` (a payload), `file` (a path inside the
bridge folder) or `deep_link` (a URL) — and become ordinary Bevro artifacts.
A failure is `{"status": "failed", "summary": "...", "failure": "invocation_failed"}`,
using the same failure categories as everything else. No bridge-specific
result rendering exists anywhere.

This maps onto the ordinary `command` runtime: standard input in JSON form,
`json` output mode. Nothing new was invented to carry it.

## Where it lives

```
data/integrations/<provider-id>/
├── bridge.py        (or bridge.js…)
├── bridge.json      Bevro's own manifest
├── README.md
└── tests/
```

`data/` is runtime data and is not tracked by git: the public repository holds
the *mechanism*, never a generated private bridge. The manifest records the
bridge version, provider id, source path, language and **fingerprint**, the
runtime kind, the command to run, input and output modes, capabilities, which
callable it uses, which provider built it, when, and the validation result. No
secrets.

## Safety

- The project path must already be inside the approved local roots
  (`BEVRO_LOCAL_ROOTS`).
- The builder is given a workspace whose **write** root is the bridge folder
  and whose **read** root is the project: `Workspace.read_paths` expresses this
  generically, and the coding adapter passes it to the tool as a read-only
  reference directory. No provider is special-cased.
- The prompt states the rules plainly: write only in the bridge folder, never
  change the project, never install into it, never read credentials, never
  copy the source in.
- Bevro **hashes every file in the project before the build**, keeps that list
  where the builder cannot reach it (`data/integrations/.snapshots/`), and
  compares afterwards.
- `git commit`, `git push`, `sudo` and the other destructive commands are
  already blocked for coding runs.

If the project changed at all, the connection is refused: nothing is
registered, the person is told *"Bevro stopped: building the connection would
have changed your project, so nothing was connected"*, and the evidence (which
files appeared or changed) is kept server-side. Files the person already had
are never touched by Bevro in the process — the protection is prevention plus
verification, not repair.

## Validation

Before a bridge becomes usable:

1. the manifest exists and names a command;
2. the bridge file exists;
3. the project is byte-for-byte as it was;
4. a sample request is run through it;
5. the answer must parse as the contract and carry a summary;
6. only then is the RuntimeProfile registered and the manifest finalised.

If any step fails, nothing is registered — a broken runtime is never left
active — and the person sees *"Bevro couldn't create a reliable connection for
this project"* with **Retry** and **Advanced setup**.

## Keeping it honest

The manifest records a fingerprint of the project's source when the bridge was
built. When the project moves on, Manage shows the connection as **Needs
review**; Bevro does not silently assume an old bridge is still right, and does
not rebuild on every change either.

**Manage → Rebuild connection** re-inspects the project: if it now has a
runtime of its own, that is preferred and the bridge stays as a fallback;
otherwise a fresh bridge is built and validated. Nothing has to be deleted or
reconnected.

## For other coding providers

To be able to build connections, a provider only needs to declare the
capability:

```json
{"id": "integration_bridge_building", "title": "Build connections"}
```

and be able to take a plain request and write files in the workspace it is
given. The request it receives is the BridgeSpec prompt above; what it must
leave behind is `bridge.py` (or another program), `bridge.json` naming the
command to run, and a README. Everything else — validation, registration,
ranking, fallback — is Bevro's side and needs no cooperation.

Tests use a stand-in builder (`api/tests/fake_builder.py`) that writes a bridge
without calling any model, so CI never spends a penny.

## Limitations

- A bridge calls one public callable with one string. A project whose useful
  entry point needs structured arguments gets defaults and a note, or fails
  validation honestly.
- Dependencies for the bridge itself are not installed automatically; the
  bridge uses the project's own environment where one exists.
- Read-only access to the project is expressed to the coding tool and verified
  afterwards; it is not enforced by the operating system.
