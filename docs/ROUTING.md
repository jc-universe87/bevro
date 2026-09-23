# Routing

Routing answers one question: *which provider should take this request?*
It is deliberately separate from execution. A router returns a
`RoutingDecision`; Bevro validates it against the provider registry and only
then creates the Task and ProviderRun that the existing services run. The
router never executes anything, never sees a filesystem path or a secret,
and cannot change task state.

```
User request
   ↓
Router  ──  DeterministicRouter (rules; default; offline; the fallback)
        └─  LLMRouter (a routing model behind the RoutingModel interface)
   ↓
RoutingDecision  →  validate against the registry
   ↓
Task → ProviderRun → Adapter → Provider → Artifact      (unchanged)
```

## Two routers

**DeterministicRouter** (`api/app/routing/deterministic.py`) maps keyword
families to *capabilities* (`coding`, `events.allocate`, `research`), then
picks an available provider that declares the capability — when several do,
the one the request actually names (by its name or a capability title, e.g.
"Ask Market Research about competitor changes") wins; otherwise catalogue
order. If no family matches, a provider the request clearly names (two or
more distinct words from its name and capability titles) is still chosen
with low confidence, which is how a newly connected agent becomes reachable
offline without a rule being written for it. It has no provider names in its
code. A request that matches nothing gets no provider — Bevro does not route
everything to Research. It is the default, needs nothing, calls nothing, and
doubles as the fallback and the test implementation.

**LLMRouter** (`api/app/routing/llm.py`) sends the request and a sanitised
provider catalogue to a routing model and expects a structured decision
back. Every decision is validated; any failure falls back to the
deterministic router.

The task service depends only on the `Router` protocol
(`api/app/routing/router.py`). Which one is active comes from configuration.

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

With `BEVRO_ROUTER_MODE=llm`, **each request typed on Home is sent to the
configured model provider**, together with the sanitised catalogue below and,
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

The person sees nothing unusual; the task simply proceeds. The task keeps
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
