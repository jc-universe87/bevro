# Routing

Routing answers two questions, in this order:

1. **Which of the person's apps and agents is for this?** Chosen on what
   each one is for, over everything in Apps & agents - including apps Bevro
   cannot send work to.
2. **How can it be used right now?** Bevro sends it the work; or the person
   opens its own app; or it needs a credential first; or it can't be reached
   right now; or here is how it's used; or it needs setting up.

Asking them in that order is the point: an app that fits but can't be
driven is still the answer, and is never swapped for a worse one that
happens to be reachable. Routing never runs anything, never sees a
filesystem path, a secret or any item's own data (notes, documents,
reports), and cannot change task state. It reads only what Bevro already
holds about each item.

```
User request
   ↓
fit       app/routing/fit.py       which item fits, and how surely
   ↓
resolve   app/routing/resolve.py   how it can be used now -> an Answer
   ↓
"direct"  →  Task → ProviderRun → Adapter → Provider → Artifact   (unchanged)
otherwise →  said on Home: open it, add a credential, try again, choose, ...
```

`POST /api/route` returns the Answer and creates nothing. Home asks it
first and starts work by itself only for a sure "direct" answer.
`POST /api/tasks` without a provider routes the same way, creates a task
only for "direct", and otherwise returns the same Answer (HTTP 503, under
`detail.answer`).

## Which one fits

`app/routing/fit.py`. Each word of the request is looked for in what Bevro
knows about each item, and counts for the best place it was found:

| Where | Weight |
|---|---|
| its name | 3.0 |
| what the person said it is for ("What should Bevro use it for?") | 3.0 |
| its capability names and the service's own terms for them | 2.5 |
| capability titles | 2.0 |
| its description | 1.5 |
| capability descriptions, and what discovery read (README and the like) | 1.0 |

Then:

- **Broad words count for less** (×0.4): "find", "review", "research",
  "report", "help", sizes and counts... They say something about almost
  any work. The verb a request *opens* with counts more (×0.7), but only
  where an item says in a sentence what it is for: "Compare these" is what
  a general research agent does; "Write a poem" is not what "Write code"
  is about.
- **Shared words count for less** when ranking: a word several items use
  tells them apart from nothing (divided by the square root of how many
  use it). It is still evidence that *something* fits.
- **How a thing is built is ignored** in README-type text: Docker,
  FastAPI, Postgres, API, endpoint...
- **A few intent concepts** add what a request means without saying it.
  They are kinds of work, never particular apps: personal knowledge
  ("where did I write about...", "what did I conclude..."), filing ("where
  is my passport scan", "filed"), career (jobs, vacancies, applications),
  market (trends, competitors), coding, scheduling, email, events. Words a
  concept adds count ×0.6 of words the person used.
- **Naming an item** settles it, unless another is named too.
- What the person typed about an item is marked as theirs (`"by":
  "person"`) and survives looking again at the service.

How sure:

| | |
|---|---|
| nothing reaches 1.0 | **none**: "I don't have an app or agent that looks suited to this yet." |
| another is within 70% of the best | **choice**: up to three, the person picks |
| "What apps can help me ...?" | **choice**: a list, never the work |
| 2.5 or more, and 1.5× the runner-up | **sure** |
| otherwise | **likely**: named, but Home asks before starting anything |

No score ever leaves the server. "Why?" is one plain sentence: what the
person said it is for, what it works with ("Career Agent works with
opportunities, vacancies and applications."), or its own description.

## How it can be used now

`app/routing/resolve.py`, for the chosen item:

| Outcome | When | Home offers |
|---|---|---|
| `direct` | Bevro can send it work now | starts it (sure), or **Use it** (likely) |
| `handoff` | no way in for Bevro; it has a web app | **Open it** |
| `blocked` | the way in needs a credential Bevro doesn't have | **Add credential**, How to use it |
| `unavailable` | the way in stopped answering, isn't running, or its helper is away | **Try again**, Open app |
| `how_to` | no app to open, but it runs on a schedule, posts results... | **How to use it** |
| `setup` | known, and nothing says how it is used | **Set up direct access** |

"Can Bevro send it work" is decided exactly as execution decides it, so a
`direct` answer is never contradicted by the task it starts; a missing
credential is checked first, because work without it can only fail.
Paused and removed items are never considered.

## The Router interface and the optional routing model

**DeterministicRouter** (`api/app/routing/deterministic.py`) is the same fit
scorer limited to what a Router may choose - providers Bevro can drive now
- and selects one only when one clearly fits. It is the default, needs
nothing, calls nothing, and is the fallback.

**LLMRouter** (`api/app/routing/llm.py`) sends the request and a sanitised
catalogue of the providers Bevro can drive to a routing model and expects a
structured decision back. It is a bounded fallback: it is asked only when
the words settle nothing, or only thinly for something Bevro would run.
Its proposal is never "sure" (Home asks first), and it never sees - so can
never overrule - an app Bevro can't drive. Obvious requests never call it.

## Configuration

```
BEVRO_ROUTER_MODE=deterministic     # default: nothing leaves the machine
BEVRO_ROUTER_MODE=llm
BEVRO_ROUTER_BACKEND=openai         # or anthropic
BEVRO_ROUTER_MODEL=                 # backend default: gpt-4o-mini / claude-haiku-4-5-20251001
BEVRO_ROUTER_API_KEY=
BEVRO_ROUTER_BASE_URL=              # e.g. http://localhost:11434/v1 for a local OpenAI-compatible server
BEVRO_ROUTER_TIMEOUT_SECONDS=8
```

Put them in `.env`; `docker-compose.yml` passes them to the API. No key is
ever required for Bevro to start: in `llm` mode without a key, every request
simply falls back to the rules (and the log says why). Settings shows
"Intelligent routing: On" or "Local routing" and nothing else.

## Privacy

With `BEVRO_ROUTER_MODE=llm`, **a request typed on Home is sent to the
configured model provider when the words alone did not settle it** (see
above; an obvious request never is), together with the sanitised catalogue below and,
when continuing a task, the previous question and the person's answer. That
is all. Provider secrets, workspace paths, adapter configuration, endpoints,
worker details, task history, artifacts and logs are never sent.

With `BEVRO_ROUTER_MODE=deterministic` no external routing API is called at
all. That is the default, and the right setting for self-hosted use where
requests must not leave the machine. Explicit "Ask" from the Agents screen
never involves the router in either mode.

## The provider catalogue

Built fresh for every routing call (`api/app/routing/catalogue.py`) from the
registry; the model may only choose ids that appear in it.

```json
{"id": "claude-code", "name": "Claude Code", "description": "Build, fix and change software",
 "capabilities": [{"id": "coding", "title": "Write code", "description": "..."}, ...],
 "available": true, "can_invoke": true, "requires": ["workspace"],
 "constraints": "Long-running; works inside one approved project directory."}
```

Unavailable providers (paused, or a coding provider with no worker running)
are filtered out before the call, so the model cannot pick them. Descriptions
are clipped; nothing from `adapter`, `app_url`, secrets or workspaces is
included. A test asserts the payload contains none of those.

## The decision schema

`api/app/routing/decision.py`, validated with Pydantic (`extra="forbid"`):

```json
{
  "selected_provider_ids": ["claude-code"],
  "needs_input": true,
  "input_request": {"kind": "workspace", "prompt": "Which project should I work on?"},
  "rationale": "Only Claude Code declares coding capabilities.",
  "plan": [{"goal": "Inspect the project"}, {"goal": "Make the change"}, {"goal": "Run checks"}],
  "confidence": 0.9,
  "reason": null
}
```

- `selected_provider_ids` is a list so that a later router can plan
  multi-provider work; today at most one is executed.
- `input_request.kind` is `workspace` (Bevro supplies the options from its
  registry) or `question` (one short free-text question).
- `plan[].provider_id` is optional and, when present, must be a selected id.
- `reason` explains an empty selection. `routing_source` is Bevro's, not the
  model's.

Output that does not validate is not repaired or guessed at; it is a
`RoutingModelError` and the fallback runs.

## Validation

`api/app/routing/validate.py` treats every decision — including the
deterministic router's — as a proposal:

| Check | On failure |
|---|---|
| at most one provider selected | reject (fallback) |
| provider id exists in the registry | reject |
| provider enabled | reject |
| provider available (worker reporting, CLI present, …) | reject |
| provider has a registered adapter | reject |
| `workspace` input only for providers that require one | reject |
| plan steps reference only selected providers | reject |
| `needs_input` has an `input_request` of a supported kind | reject |
| empty selection | accepted as "nothing suitable" (a stray `needs_input` is ignored) |

A rejected LLM decision falls back to the rules. A rejected deterministic
decision (which should not happen) becomes the calm "I don't have a connected
provider for that yet."

The workspace question is Bevro's to ask: even when the model says
`needs_input: workspace`, the task service first tries to infer the project
from the request and only asks if it cannot. When the model asks a
`question`, the task pauses in `needs_input` with that question; the answer
is stored on the run (`input.answer`) and the task is queued for the already
chosen provider.

## Fallback

```
LLMRouter.route
  ├─ model timeout / HTTP error / auth failure / non-JSON / schema failure  → RoutingModelError
  ├─ decision rejected by validation                                        → InvalidDecision
  └─ any other exception                                                    → logged
        ↓ all of the above
  DeterministicRouter.decide(request)  with routing_source = "fallback"
```

It is only asked when the words were not enough (see above). The person
sees nothing unusual: the words' answer stands. The task keeps
`routing.source = "fallback"` and a short `fallback_reason` (e.g.
`RoutingModelError: routing model timed out`). Home never waits longer than
`BEVRO_ROUTER_TIMEOUT_SECONDS` for the model.

## What is stored and logged

On each task, `tasks.routing` (internal, never sent to the browser):
`source` (deterministic / llm / fallback / explicit), `router_version`,
`backend`, `selected_provider_ids`, `confidence`, `rationale`, `plan`,
`fallback_reason`. Prompts and raw model responses are not stored.

Logger `bevro.routing`: routing started (backend, provider ids offered),
success (selection, needs_input, confidence, elapsed), fallback with reason,
validation rejections. Keys are never logged.

## The prompt

`api/app/routing/prompt.py` holds `ROUTER_SYSTEM_PROMPT` and
`ROUTER_PROMPT_VERSION`. Bump the version whenever the wording changes and
note it here.

| Version | Change |
|---|---|
| 1 | Initial prompt: select from the catalogue only, prefer capability fit, at most one provider, empty selection when nothing fits, ask only when necessary, return only the schema. |

## Cost and context

Routing is classification, not execution. One call sends the system prompt
(~200 words), the request (clipped to 2,000 characters) and the catalogue
(three shipped providers ≈ 600 characters), and asks for at most 400 output
tokens at temperature 0. With the default small models a call takes about
1–3 seconds and a fraction of a cent. No source code, workspace contents,
history or artifacts are ever sent. Claude Code is never used for routing.

## Adding a routing-model backend

1. Add `api/app/routing/models/<name>.py` with a class exposing `name` and
   `route(request, catalogue, context) -> RoutingDecision`. Build the user
   message with `_shared.user_message` and parse the answer with
   `_shared.parse_decision` so the context and the strictness stay identical
   across vendors. Raise `RoutingModelError` for anything that goes wrong.
2. Register it in `get_routing_model()` (`models/__init__.py`).
3. Test it with `httpx.MockTransport` as `test_routing.py` does; never call
   the real service in the normal suite.

The OpenAI backend already covers any OpenAI-compatible server
(`BEVRO_ROUTER_BASE_URL`), which is the easiest way to route locally.

## Towards multi-provider planning

The schema already carries what a planner needs: a list of providers and a
`plan` whose steps may name a provider:

```json
{"selected_provider_ids": ["research", "claude-code"],
 "plan": [{"provider_id": "research", "goal": "Identify implementation options"},
          {"provider_id": "claude-code", "goal": "Implement the selected approach"}]}
```

Today such a decision is rejected (`MAX_SELECTED = 1`) and the fallback
runs. The next step is an execution layer that creates one ProviderRun per
plan step, feeds each step's artifacts into the next run's `input`, and
moves the task through `working` → `waiting` between steps — all on the
existing Task / ProviderRun / Artifact model, with the router untouched.
