# Building a provider

A provider is anything that can do work for a Bevro task. This page is
everything you need to add one; you should not have to read the rest of the
source tree.

There are three ways in, from least to most code:

| Route | You write | Good for |
|---|---|---|
| **Connect** screen | nothing — fill in a form | an existing service that speaks HTTP (see `examples/http-provider-contract.md`) |
| **Built-in provider** | a JSON manifest + one Python function | anything you can call from Python |
| **New adapter** | a class implementing the adapter contract | a new *kind* of transport or execution model (MCP, another coding CLI, …) |

This page covers the second and third.

## 1. The manifest

`providers/manifests/<slug>.json`:

```json
{
  "slug": "weather",
  "name": "Weather",
  "description": "Forecasts and warnings",
  "enabled": true,
  "capabilities": [
    {"id": "weather.forecast", "title": "Forecast", "description": "Forecast for a place and date."}
  ],
  "adapter": {"kind": "local", "ref": "providers.weather:run", "config": {}},
  "app_url": null,
  "icon": {"kind": "letter", "text": "W"},
  "origin": "example"
}
```

- `slug` is the stable identifier; `name` and `description` are what people see.
- `capabilities[]` is free-form JSON. Each needs an `id`; `title`,
  `description` and a JSON-Schema `input` are optional. There is no fixed
  list — declare what you do.
- `adapter.kind` names the transport. `local` means "a Python callable in
  this process", `ref` being `"module:function"`.
- `adapter.config` holds non-secret settings. Never credentials.
- `app_url`, if set, gives the provider an "Open ↗" action and lets it build
  deep links.

The API seeds every manifest in that directory on start (insert if the slug
is new). Restart it: `docker compose restart api`.

## 2. Capabilities

Capabilities are how Bevro decides who could take a request. The demo router
looks for `coding`, `calendar.schedule` and `research`; a real router will
match on whatever you declare. Use dotted ids for specific actions
(`calendar.schedule`) and plain ones for broad abilities (`research`).

Only claim what your implementation genuinely does.

## 3. The adapter contract

`adapters/base.py` is the whole boundary. A provider handler behind the
`local` adapter is a function:

```python
from adapters import ArtifactDraft, InvocationContext, InvocationRequest, InvocationResult, ProviderSpec, ResultState


def run(request: InvocationRequest, provider: ProviderSpec, context: InvocationContext | None = None) -> InvocationResult:
    ...
```

- `request.request` is the text the person typed; `request.task_id` and
  `request.run_id` identify the work; `request.input` carries anything
  Bevro attached (for coding providers: the resolved workspace and
  permissions); `request.secrets` holds this provider's stored credentials,
  decrypted for this call only — never persist them.
- `provider` is what Bevro knows about you: capabilities, `adapter.config`,
  `app_url`.
- `context` (optional) is for long runs: `context.progress("Checking the
  forecast")` shows a short phase to the person; `context.cancelled()` tells
  you to stop.

A new **adapter** implements the same idea for a whole family of providers:

```python
class MyAdapter:
    kind = "my_transport"
    execution = "inline"           # or "background" if runs take minutes (a worker picks them up)
    requires = frozenset()         # e.g. {"workspace"}: Bevro supplies it or asks the person

    def check(self, provider, secrets) -> HealthResult: ...
    def invoke(self, provider, request, context=None) -> InvocationResult: ...
    def get_status(self, provider, external_ref, secrets) -> InvocationResult: raise NotSupported(...)
    def cancel(self, provider, external_ref, secrets) -> bool: raise NotSupported(...)

register_adapter(MyAdapter())   # and import the module in adapters/registry.py
```

## 4. Invocation and results

Return an `InvocationResult`:

```python
return InvocationResult(
    state=ResultState.COMPLETED,                       # or FAILED, NEEDS_INPUT, NEEDS_APPROVAL, RUNNING, CANCELLED
    summary="Done. Rain from 15:00, clearing by evening.",   # one plain sentence; this is what the person reads first
    artifacts=[...],
    external_ref=None,                                 # a handle for get_status / cancel, if you have one
    metadata={},                                       # execution details kept on the run, never shown
)
```

`summary` is the headline. Write it for a person, not a log: what happened,
in one line. Bevro maps your state onto the task's state machine; you never
touch `Task` yourself.

## 5. Artifacts

Anything worth keeping is an `ArtifactDraft`:

```python
ArtifactDraft(type="report", title="Forecast for Oslo", mime_type="text/markdown", payload={"text": "## Tomorrow\n…"})
ArtifactDraft(type="structured", title="Hourly", payload={"columns": ["Hour", "°C"], "rows": [["09:00", 12], ["12:00", 15]]})
ArtifactDraft(type="file", title="forecast.csv", mime_type="text/csv", content=b"...", filename="forecast.csv")
ArtifactDraft(type="deep_link", title="Open in the weather app", external_url="https://weather.example/oslo")
```

Bevro stores `content` on its own filesystem and everything else in the
database. The types it renders itself: `text`, `note`, `report` (Markdown
when `mime_type` says so), `structured` (a table), `image`, `file`, `diff`,
`deep_link`. Any other `type` is stored just the same and shown as
**Open result ↗** if it has somewhere to open — so you can return things
Bevro does not understand yet.

Do not duplicate large data into artifacts when a link or a small structured
summary will do.

## 6. Errors

Return `ResultState.FAILED` with a plain `error` sentence. That sentence is
shown to the person; keep the technical detail in your own log. If your code
raises instead, Bevro catches it, logs the traceback server-side and shows
"<Provider> could not complete this request." — but a deliberate message is
better.

For things you cannot decide alone, return `NEEDS_INPUT` or
`NEEDS_APPROVAL` with the question as the `summary`.

## 7. Deep links

A `deep_link` artifact with an `external_url` becomes the headline action of
the result ("Open in Calendar →"). Build it from `provider.app_url` plus a
path in `adapter.config` so that the configuration, not the code, decides
where the application lives. `providers/calendar_demo.py` shows the pattern.

## 8. UI

You do not write UI. A provider's presence in Bevro is its name,
description and the actions it really supports ("Ask", and "Open ↗" if it has
an `app_url`). Results are rendered from artifact types. If you need a new
renderable type, add it to `KNOWN_TYPES` in `api/app/services/artifacts.py`
and a branch in `web/src/components/ArtifactView.tsx` — and keep the
fallback working.

## 9. Tests

Add a test in `api/tests/` that submits a request routed to your provider,
executes the run, and checks the summary and artifact types:

```python
def test_weather_answers(seeded):
    task = task_service.submit(seeded, "Forecast for Oslo tomorrow", provider=provider_service.get_by_slug(seeded, "weather"))
    seeded.commit()
    task_service.execute_run(seeded, task.runs[0].id)
    seeded.refresh(task)
    assert task.state == "completed"
    assert task.summary.startswith("Done.")
    assert {a.type for a in task.artifacts} == {"report", "structured"}
```

Tests must not call paid or external services; use fixtures or a fake
(`api/tests/fake_claude.py` is an example of faking a whole CLI).

## A complete minimal example

`providers/manifests/echo.json`:

```json
{"slug": "echo", "name": "Echo", "description": "Repeats what you say", "enabled": true,
 "capabilities": [{"id": "echo", "title": "Echo"}],
 "adapter": {"kind": "local", "ref": "providers.echo:run"}, "icon": {"kind": "letter", "text": "E"}, "origin": "example"}
```

`providers/echo.py`:

```python
from adapters import ArtifactDraft, InvocationRequest, InvocationResult, ProviderSpec, ResultState


def run(request: InvocationRequest, provider: ProviderSpec) -> InvocationResult:
    words = len(request.request.split())
    return InvocationResult(
        state=ResultState.COMPLETED,
        summary=f"Done. Echoed {words} words.",
        artifacts=[ArtifactDraft(type="note", title="Echo", mime_type="text/plain", payload={"text": request.request})],
    )
```

Restart the API. Echo appears under Agents with an "Ask" action.
