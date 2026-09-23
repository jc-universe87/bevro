# Create

**Connect** is *"I already have something."* **Create** is *"I want something
new."* They meet at discovery: from there on, an agent Bevro built is an
ordinary provider with ordinary runtimes, and nothing downstream knows the
difference.

```
Create:   intent → AgentSpec → builder → generated project → discovery → Provider
Connect:  existing project → discovery (or a bridge) → Provider
                                   ↑ they converge here
```

## What the person does

```
Create

What should this agent do?
[ Describe the work you want it to handle... ]
[ Create ]
```

No language, model, framework, runtime, adapter or repository question — Bevro
infers all of that. What comes back is a description to agree to:

```
Market Watch
Tracks competitors and relevant changes in event management software.

Can:
• Research
• Competitor analysis
• Reports

Needs:
• Web access
• Write reports

Produces: a short report

[ Create agent ]
```

Then calm progress — *Preparing agent… Designing · Building · Testing ·
Connecting* — and finally **Created**, with **Ask** and **View agent**. No
terminal output at any point.

## AgentSpec

`api/app/create/spec.py`. The agreed description, and the only thing the
builder is told:

| | |
|---|---|
| name, description, purpose | what it is and what it is for |
| capabilities | 1–4, each with an id and a title; these become the provider's capabilities |
| input / output expectation | a plain request in; report, text, structured, file or summary out |
| permissions | `web`, `files_read`, `files_write`, `run_commands`, `external_api`, `schedule` — shown as *Web access*, *Write reports*… |
| integrations | outside services the work needs, by name |
| schedule | plain words when recurring work was asked for |
| persistence, complexity, constraints | whether it must remember anything, how big it is, anything else it must respect |

`preview()` is the only view the person sees: names and sentences, never the
spec itself.

### Where it comes from

A **SpecModel** (`api/app/create/specmodel.py`), provider-neutral like the
router's:

- `LLMSpecModel` — one structured completion through whichever backend routing
  is configured with. It receives the person's sentence and nothing else, and
  is told plainly that it never writes code or designs an implementation.
- `RulesSpecModel` — local rules, the default when no model is configured, and
  the fallback whenever the model is slow, unreachable or unusable. Create
  works offline.

## Who builds it

By capability, never by name: `agent_building`, then `coding`,
`repository_changes`, `software_build`. Whichever available provider offers
one is asked. If none does:

> Creating agents needs a connected coding agent.
> [ Connect coding agent ] [ Cancel ]

Building is ordinary Bevro work — a Task with a ProviderRun, visible under
Recent as "Creating Market Watch".

## What the builder is told

A **BuildSpec** (`api/app/create/build.py`): the AgentSpec, the one directory
it may write in, and the contract the project must satisfy:

1. one obvious way to give it a request — MCP, a small HTTP service, or a
   command-line program;
2. a request is one plain sentence; the answer is text, and may produce files;
3. declared metadata (`pyproject.toml`, `package.json`…) including dependencies;
4. a README saying what it does, how to run it, and which environment
   variables it needs;
5. tests that pass without network access;
6. credentials read from the environment — never written into a file;
7. no path outside its own directory, and no shell built from a request;
8. it must still work if the folder is copied elsewhere — **no dependency on
   Bevro**.

That last point matters: a created agent is a real independent project that
Bevro happens to keep, exactly as a connected one is a real project Bevro
happens to reach.

## Where it lives

```
data/agents/<provider-id>/
├── versions/<build-id>/     the project itself: source, tests, README, metadata
│   └── agent.json           Bevro's own note (spec, builder, runtime, validation)
└── current -> versions/<build-id>
```

Runtime data, gitignored: the public repository holds the mechanism, never a
generated agent. Bevro's own managed folders are approved roots by their
nature, so nobody has to list their home directory to let Bevro run something
it wrote itself; the person's own projects still require `BEVRO_LOCAL_ROOTS`.

## Validation, then ordinary discovery

Before a created agent exists as far as the rest of Bevro is concerned:

1. **security scan** of the generated source — a key or token written into a
   file, `os.system`/`shell=True`/`eval`/`exec`, or a path that only exists on
   this machine, all fail the build;
2. **its own tests**, run by the command its README states;
3. **Bevro's ordinary discovery** (`app.connect`) is pointed at the folder — the
   same code that inspects a project someone connects. If it cannot find a
   runtime, the build has failed. *No runtime is ever registered by hand.*
4. **one safe sample request** through the runtime discovery chose, checked for
   a usable answer;
5. only then is the provider activated, with the capabilities from the
   AgentSpec — not whatever the builder decided to write.

If any step fails: *"Bevro couldn't finish creating this agent"* with **Retry**
and **Review details**. Nothing half-active is ever left behind.

## Versions and rebuilding

Each attempt is an `AgentBuild`: spec, spec version, build number, builder,
task, directory, validation result, source fingerprint, and whether it is
active. **Rebuild** (Agents → Manage) builds a new version *beside* the working
one and swaps `current` only after it passes — a failed rebuild leaves the
agent you have running, and says so. **Edit purpose** rewrites the AgentSpec
from a new sentence and rebuilds the same way; the provider keeps its identity
and its history.

## Credentials and schedules

Credentials are asked for only when they are actually needed: the generated
project declares what it wants in the environment, discovery picks that up, and
the ordinary credential handling takes over (*OpenAI credential · Missing*,
added under Manage). Nothing is ever baked into generated source — the scan
fails the build if it is.

If the person asked for recurring work, the AgentSpec records the intent and
the builder is told **not** to build a scheduler: the agent does the work, and
Bevro owns when it runs. Once the agent is created, set the timetable up under
**Scheduled** — the same Automation model a connected agent uses
([AUTOMATIONS.md](AUTOMATIONS.md)). This is what keeps a created agent
portable: nothing about your timetable is baked into its source.

## V1 scope

Agents that take a text request, do work, and return text, structured data or
files. Not: persistent multi-agent societies, long-term memory frameworks,
custom visual mini-apps, or self-modifying agents.

## For other coding providers

Declare the capability:

```json
{"id": "agent_building", "title": "Build agents"}
```

take a plain request, and write files in the workspace you are given. The
request is the BuildSpec prompt above; what you leave behind must satisfy the
contract. Everything else — validation, discovery, registration, ranking,
routing — is Bevro's side.

Tests use a stand-in builder (`api/tests/fake_agent_builder.py`) that writes a
real small project without calling any model, so CI never spends a penny.

## Security

The builder may write only in the build directory it is given, and is given no
read access to anything else. `sudo`, `git push` and the other destructive
commands are blocked for coding runs, as always. The generated source is
scanned before anything is activated, and the agent is run afterwards through
the ordinary runtime rules — the same approved-roots and credential handling as
every other provider.
