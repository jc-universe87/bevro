"""Running work against a service that describes itself with OpenAPI.

Some services do not take a prompt. They take operations: list the things,
create a job, run it, fetch the report. A piece of work is two or three of
those in order, and this adapter is what runs them.

    OperationPlan  →  validate every step against the catalogue
                   →  call, in order, carrying results forward
                   →  one summary and ordinary artifacts

The plan is a *proposal*. Nothing in it is trusted: every step must name an
operation that the descriptor actually described, every argument must fit
that operation's schema, and the request goes to the service's own base URL.
A plan cannot invent a URL, a method or a host, so a confused planner can at
worst call the wrong described operation - never something undescribed.

The whole plan is one ProviderRun. Several HTTP calls are how the work was
done, not several pieces of work.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from adapters.base import (
    ArtifactDraft,
    FailureKind,
    InvocationContext,
    InvocationRequest,
    InvocationResult,
    HealthResult,
    ProviderSpec,
    ResultState,
)
from adapters.registry import register_adapter
from adapters.runtime import BaseRuntimeAdapter

log = logging.getLogger("bevro.adapters.openapi")

# A task is a handful of operations, not a program.
MAX_STEPS = 6
TIMEOUT_SECONDS = 120.0
# What one response may contribute to a summary before it is cut.
PREVIEW_CHARS = 1200

READ_ONLY = "read_only"
WORK_EXECUTION = "work_execution"
STATE_CHANGE = "state_change"
EXTERNAL_ACTION = "external_action"
UNKNOWN = "unknown"

_PATH_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class PlanError(Exception):
    """A proposed plan does not fit what the service described."""


class PlannedStep(BaseModel):
    """One operation to call, with where its arguments come from."""

    operation_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # {"run_id": "id"} - take `id` out of this step's result and remember it
    # as `run_id` for later steps.
    extract: dict[str, str] = Field(default_factory=dict)
    # What to tell the person while this runs. Plain words.
    label: str | None = None


class OperationPlan(BaseModel):
    steps: list[PlannedStep] = Field(default_factory=list)
    # Why these steps, in one sentence, for the run's own record.
    reason: str = ""


# --------------------------------------------------------------------------- validation

def _operations(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(op["operation_id"]): op for op in config.get("operations") or [] if isinstance(op, dict) and op.get("operation_id")}


def _coerce(value: Any, wanted: str) -> Any:
    """Make a value fit the type the schema asked for, or leave it alone."""
    if wanted in ("integer", "number") and isinstance(value, str):
        try:
            return int(value) if wanted == "integer" else float(value)
        except ValueError:
            return value
    if wanted == "boolean" and isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return value


def validate_plan(plan: OperationPlan, config: dict[str, Any], *, allow: set[str] | None = None) -> OperationPlan:
    """Check a proposed plan against what the service actually described.

    Raises rather than guessing. An operation that is not in the catalogue,
    a missing required argument, or a step whose safety class this run is not
    allowed to touch, all stop the plan before anything is called.
    """
    catalogue = _operations(config)
    if not plan.steps:
        raise PlanError("The plan had no steps.")
    if len(plan.steps) > MAX_STEPS:
        raise PlanError(f"A plan may have at most {MAX_STEPS} steps; this one had {len(plan.steps)}.")

    context = dict(config.get("context") or {})
    known = set(context)
    checked: list[PlannedStep] = []
    for position, step in enumerate(plan.steps, start=1):
        op = catalogue.get(step.operation_id)
        if op is None:
            raise PlanError(f"Step {position} names an operation this service does not describe.")
        safety = str(op.get("safety") or UNKNOWN)
        if allow is not None and safety not in allow:
            raise PlanError(f"Step {position} would {_in_words(safety)}, which this request did not ask for.")

        arguments = dict(step.arguments)
        # A scope the connection already has is filled in rather than invented.
        parameters = op.get("parameters") if isinstance(op.get("parameters"), dict) else {}
        body = op.get("body") if isinstance(op.get("body"), dict) else {}
        for name in list(parameters) + list(body):
            if name not in arguments and name in context:
                arguments[name] = context[name]

        for name, spec in parameters.items():
            if spec.get("required") and name not in arguments and name not in known:
                raise PlanError(f"Step {position} is missing {name}, which that operation needs.")
            if name in arguments:
                arguments[name] = _coerce(arguments[name], str(spec.get("type") or "string"))
        for name, spec in body.items():
            if spec.get("required") and name not in arguments and name not in known:
                raise PlanError(f"Step {position} is missing {name}, which that operation needs.")
            if name in arguments:
                arguments[name] = _coerce(arguments[name], str(spec.get("type") or "string"))
                allowed = spec.get("enum")
                if isinstance(allowed, list) and allowed and str(arguments[name]) not in [str(a) for a in allowed]:
                    raise PlanError(f"Step {position} gave {name} a value that operation does not accept.")

        # Every path placeholder must be fillable, now or by an earlier step.
        for placeholder in _PATH_PARAM.findall(str(op.get("path") or "")):
            if placeholder not in arguments and placeholder not in known:
                raise PlanError(f"Step {position} cannot say which {placeholder.replace('_', ' ')} to use.")
        known.update(step.extract.keys())
        checked.append(PlannedStep(operation_id=step.operation_id, arguments=arguments, extract=dict(step.extract), label=step.label))
    return OperationPlan(steps=checked, reason=plan.reason)


def _in_words(safety: str) -> str:
    return {
        READ_ONLY: "read something",
        WORK_EXECUTION: "run work",
        STATE_CHANGE: "change something",
        EXTERNAL_ACTION: "contact someone outside",
        UNKNOWN: "do something it does not describe",
    }.get(safety, "do something unexpected")


# --------------------------------------------------------------------------- a deterministic plan

def overlap_of(wanted: set[str], words: set[str]) -> int:
    """How much two sets of words agree, allowing for word endings.

    A person asks for "statistics" and the service calls it "stats"; the same
    idea, and a shared stem is enough to see it. Nothing here is a dictionary
    of synonyms - only the words each side actually used.
    """
    score = 0
    for want in wanted:
        for word in words:
            if want == word or (len(want) >= 4 and len(word) >= 4 and (want.startswith(word[:4]) or word.startswith(want[:4]))):
                score += 1
                break
    return score




def plan_from_words(request: str, config: dict[str, Any]) -> OperationPlan:
    """A single read operation, chosen by matching words. No model needed.

    This is what answers "show me my opportunities" on an installation with
    no routing model configured. It only ever proposes reads, and only one.
    """
    wanted = {w for w in re.findall(r"[a-z]{4,}", request.lower())}
    best: tuple[float, dict[str, Any]] | None = None
    for op in config.get("operations") or []:
        if not isinstance(op, dict) or str(op.get("safety")) != READ_ONLY:
            continue
        text = f"{op.get('operation_id', '')} {op.get('path', '')} {op.get('summary', '')} {' '.join(op.get('tags') or [])}".lower()
        words = set(re.findall(r"[a-z]{4,}", text))
        overlap = overlap_of(wanted, words)
        if not overlap:
            continue
        # Prefer an operation whose scope the connection can already fill.
        context = set(config.get("context") or {})
        missing = [p for p in _PATH_PARAM.findall(str(op.get("path") or "")) if p not in context]
        score = overlap - 2.0 * len(missing) - 0.01 * len(str(op.get("path") or ""))
        if best is None or score > best[0]:
            best = (score, op)
    if best is None or best[0] <= 0:
        raise PlanError("Bevro couldn't tell which of this service's operations would answer that.")
    return OperationPlan(steps=[PlannedStep(operation_id=str(best[1]["operation_id"]), label=str(best[1].get("summary") or "")[:80])], reason="Matched one read operation by its description.")


def plan_work_chain(request: str, config: dict[str, Any]) -> OperationPlan:
    """A create → execute → read chain, worked out from the paths themselves.

    Plenty of operational APIs share one shape: post to a collection to make
    a job, post again under that job to run it, then read what it produced.
    That shape is visible in the descriptor - a work operation whose path
    needs an id, a creator at the collection above it, and a read below it -
    so a plan for it does not need a model.
    """
    catalogue = [op for op in config.get("operations") or [] if isinstance(op, dict)]
    context = set(config.get("context") or {})
    wanted = {w for w in re.findall(r"[a-z]{4,}", request.lower())}

    def words(op: dict[str, Any]) -> set[str]:
        text = f"{op.get('operation_id', '')} {op.get('path', '')} {op.get('summary', '')} {' '.join(op.get('tags') or [])}"
        return set(re.findall(r"[a-z]{4,}", text.lower()))

    work = [op for op in catalogue if str(op.get("safety")) == WORK_EXECUTION]
    ranked = sorted(work, key=lambda op: -(len(wanted & words(op)) + 0.5 * len(set(op.get("tags") or []) & wanted)))
    for chosen in ranked[:6]:
        path = str(chosen.get("path") or "")
        unresolved = [p for p in _PATH_PARAM.findall(path) if p not in context]
        if len(unresolved) != 1:
            continue
        needed = unresolved[0]
        collection = path.split("{" + needed + "}")[0].rstrip("/")
        creator = next(
            (
                op
                for op in catalogue
                if op.get("method") == "POST"
                and str(op.get("path") or "").rstrip("/") == collection
                and str(op.get("safety")) in (WORK_EXECUTION, STATE_CHANGE)
                and not [p for p in _PATH_PARAM.findall(str(op.get("path") or "")) if p not in context]
            ),
            None,
        )
        if creator is None:
            continue
        field = next((f for f in creator.get("returns_fields") or [] if f in (needed, "id", needed.replace("_id", ""))), "id")
        steps = [
            PlannedStep(operation_id=str(creator["operation_id"]), extract={needed: field}, label=str(creator.get("summary") or "")[:80]),
            PlannedStep(operation_id=str(chosen["operation_id"]), label=str(chosen.get("summary") or "")[:80]),
        ]
        # Anything below the same job that reads out what it produced.
        report = next(
            (
                op
                for op in catalogue
                if op.get("method") == "GET"
                and str(op.get("safety")) == READ_ONLY
                and str(op.get("path") or "").startswith(collection + "/{" + needed + "}")
                and re.search(r"report|result|summary|output", f"{op.get('path')} {op.get('summary')}", re.I)
            ),
            None,
        )
        if report is not None:
            steps.append(PlannedStep(operation_id=str(report["operation_id"]), label=str(report.get("summary") or "")[:80]))
        return OperationPlan(steps=steps, reason="The service describes making one of these, running it, and reading the result.")

    # No chain: a single operation that needs nothing Bevro cannot supply.
    for chosen in ranked[:4]:
        if not [p for p in _PATH_PARAM.findall(str(chosen.get("path") or "")) if p not in context]:
            return OperationPlan(
                steps=[PlannedStep(operation_id=str(chosen["operation_id"]), label=str(chosen.get("summary") or "")[:80])],
                reason="One operation the service describes as doing this work.",
            )
    raise PlanError("Bevro couldn't work out how to run that against this service.")


# --------------------------------------------------------------------------- running it

def _fill(path: str, arguments: dict[str, Any], memory: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Put path parameters in the URL; the rest stay as query or body."""
    used: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        used.add(name)
        value = arguments.get(name, memory.get(name, ""))
        return str(value)

    filled = _PATH_PARAM.sub(replace, path)
    return filled, {k: v for k, v in arguments.items() if k not in used}


def _extract(result: Any, wanted: dict[str, str]) -> dict[str, Any]:
    """Pull named values out of a result so later steps can use them."""
    out: dict[str, Any] = {}
    for name, where in wanted.items():
        value: Any = result
        for part in str(where).split("."):
            if isinstance(value, list) and part.isdigit():
                value = value[int(part)] if int(part) < len(value) else None
            elif isinstance(value, list) and value:
                value = value[0]
                if isinstance(value, dict):
                    value = value.get(part)
            elif isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
            if value is None:
                break
        if value is not None and not isinstance(value, (dict, list)):
            out[name] = value
    return out


def _artifact_for(op: dict[str, Any], payload: Any, body_text: str, content_type: str) -> ArtifactDraft | None:
    """Turn one response into something a person can read.

    A list becomes a table, Markdown becomes a report, an object becomes a
    small set of fields. A wall of JSON is never the answer.
    """
    title = str(op.get("summary") or op.get("operation_id") or "Result")[:120]
    if "markdown" in content_type or (isinstance(payload, str) and payload.lstrip().startswith("#")):
        text = payload if isinstance(payload, str) else body_text
        return ArtifactDraft(type="report", title=title, mime_type="text/markdown", payload={"text": text[:200_000]}, content=text.encode("utf-8"), filename="report.md")

    rows = payload if isinstance(payload, list) else None
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                rows = value
                break
    if rows and isinstance(rows[0], dict):
        columns = [c for c in list(rows[0])[:6]]
        return ArtifactDraft(
            type="structured",
            title=title,
            summary=f"{len(rows)} {'row' if len(rows) == 1 else 'rows'}",
            payload={"columns": [c.replace("_", " ") for c in columns], "rows": [[_cell(row.get(c)) for c in columns] for row in rows[:200]]},
        )
    if isinstance(payload, dict) and payload:
        flat = {k: v for k, v in payload.items() if not isinstance(v, (dict, list))}
        if flat:
            return ArtifactDraft(type="structured", title=title, payload=flat)
    if isinstance(payload, str) and payload.strip():
        return ArtifactDraft(type="text", title=title, payload={"text": payload[:100_000]})
    return None


def _cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return f"{len(value)} items"
    return "" if value is None else value


class OpenApiAdapter(BaseRuntimeAdapter):
    """Runs a validated OperationPlan against a described HTTP service."""

    kind = "openapi"
    execution = "inline"
    requires: tuple[str, ...] = ()

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    # ------------------------------------------------------------------ health

    def health(self, provider: ProviderSpec, secrets: dict[str, str] | None = None) -> HealthResult:
        """Reachable, still described, and one safe read still works.

        Nothing is created and nothing is changed: a connection test that
        starts work is not a test.
        """
        config = provider.adapter_config
        base = str(config.get("base_url") or "").rstrip("/")
        if not base:
            return HealthResult(ok=False, state="unavailable", detail="No address recorded for this connection.")
        try:
            with self._client(config, secrets or {}) as client:
                descriptor = str(config.get("descriptor_url") or "")
                if descriptor:
                    response = client.get(descriptor)
                    if response.status_code >= 400 or "json" not in response.headers.get("content-type", ""):
                        return HealthResult(ok=False, state="unavailable", detail="The service no longer describes itself where it did.")
                probe = self._safe_probe(config)
                if probe is None:
                    return HealthResult(ok=True, state="available", detail="Described, with no safe read to try.")
                path, _ = _fill(str(probe.get("path") or "/"), dict(config.get("context") or {}), {})
                answer = client.request(str(probe.get("method") or "GET"), base + path)
                if answer.status_code >= 400:
                    return HealthResult(ok=False, state="unavailable", detail=f"A read that should work answered {answer.status_code}.")
                scope = self._scope_still_valid(answer, config)
                if scope is False:
                    return HealthResult(ok=False, state="unavailable", detail="The profile this connection uses is no longer there.")
        except httpx.HTTPError as exc:
            return HealthResult(ok=False, state="unavailable", detail=f"Couldn't reach it ({type(exc).__name__}).")
        return HealthResult(ok=True, state="available")

    def _safe_probe(self, config: dict[str, Any]) -> dict[str, Any] | None:
        """The cheapest read whose parameters the connection can already fill."""
        context = set(config.get("context") or {})
        best: dict[str, Any] | None = None
        for op in config.get("operations") or []:
            if not isinstance(op, dict) or str(op.get("safety")) != READ_ONLY or op.get("method") != "GET":
                continue
            if any(p not in context for p in _PATH_PARAM.findall(str(op.get("path") or ""))):
                continue
            if best is None or len(str(op.get("path"))) < len(str(best.get("path"))):
                best = op
        return best

    def _scope_still_valid(self, response: httpx.Response, config: dict[str, Any]) -> bool | None:
        """If this read lists the things a scope names, is the scope still one?"""
        context = config.get("context") or {}
        if not context:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        listed = json.dumps(payload)
        for value in context.values():
            if isinstance(value, str) and value and f'"{value}"' not in listed:
                return None  # this read simply does not list them; not evidence either way
        return True

    # ------------------------------------------------------------------ invoke

    def invoke(self, provider: ProviderSpec, request: InvocationRequest, context: InvocationContext | None = None) -> InvocationResult:
        config = provider.adapter_config
        base = str(config.get("base_url") or "").rstrip("/")
        if not base:
            return self._failed("This connection has no address recorded.", FailureKind.CONFIGURATION_PROBLEM)

        # Planning happens before the adapter is called; if it could not find
        # a way to answer, that is the answer.
        refused = (request.input or {}).get("operation_plan_error")
        if refused:
            return self._failed(str(refused), FailureKind.CONFIGURATION_PROBLEM)
        plan_input = (request.input or {}).get("operation_plan")
        try:
            plan = OperationPlan.model_validate(plan_input) if plan_input else plan_from_words(request.request, config)
            allowed = set((request.input or {}).get("allowed_safety") or [READ_ONLY, WORK_EXECUTION])
            plan = validate_plan(plan, config, allow=allowed)
        except PlanError as exc:
            return self._failed(str(exc), FailureKind.CONFIGURATION_PROBLEM)
        except Exception as exc:  # noqa: BLE001 - a malformed plan is not a crash
            return self._failed(f"Bevro couldn't make sense of the plan for that ({type(exc).__name__}).", FailureKind.CONFIGURATION_PROBLEM)

        memory: dict[str, Any] = dict(config.get("context") or {})
        artifacts: list[ArtifactDraft] = []
        trace: list[dict[str, Any]] = []
        summary_bits: list[str] = []
        try:
            with self._client(config, request.secrets or {}) as client:
                for position, step in enumerate(plan.steps, start=1):
                    op = _operations(config)[step.operation_id]
                    if context is not None:
                        context.progress(step.label or str(op.get("summary") or "Working")[:80])
                        if context.cancelled():
                            return InvocationResult(state=ResultState.CANCELLED, summary="Stopped before it finished.")
                    arguments = {**step.arguments}
                    for name in list(arguments):
                        if isinstance(arguments[name], str) and arguments[name].startswith("$"):
                            arguments[name] = memory.get(arguments[name][1:], arguments[name])
                    path, rest = _fill(str(op.get("path") or "/"), {**memory, **arguments}, memory)
                    method = str(op.get("method") or "GET").upper()
                    body_fields = set((op.get("body") or {}))
                    body = {k: v for k, v in rest.items() if k in body_fields} or None
                    query = {k: v for k, v in rest.items() if k not in body_fields} or None
                    response = client.request(method, base + path, params=query, json=body if method != "GET" else None)
                    trace.append({"step": position, "operation_id": step.operation_id, "method": method, "path": path, "status": response.status_code})
                    if response.status_code >= 400:
                        detail = self._detail(response)
                        return InvocationResult(
                            state=ResultState.FAILED,
                            summary=f"{op.get('summary') or step.operation_id} didn't work: the service answered {response.status_code}.",
                            error=detail,
                            failure=FailureKind.INVOCATION_FAILED,
                            metadata={"operations": trace},
                        )
                    payload, text = self._body(response)
                    memory.update(_extract(payload, step.extract))
                    artifact = _artifact_for(op, payload, text, response.headers.get("content-type", ""))
                    if artifact is not None:
                        artifacts.append(artifact)
                    summary_bits.append(self._said(op, payload))
        except httpx.HTTPError as exc:
            return self._failed(f"Couldn't reach the service ({type(exc).__name__}).", FailureKind.PROVIDER_UNAVAILABLE)

        app_url = str(config.get("app_url") or "")
        if app_url:
            artifacts.append(ArtifactDraft(type="deep_link", title="Open in the service", summary="See the full detail where it lives", external_url=app_url))
        return InvocationResult(
            state=ResultState.COMPLETED,
            summary=" ".join(b for b in summary_bits if b)[:600] or "Done.",
            artifacts=artifacts,
            metadata={"operations": trace, "plan_reason": plan.reason},
        )

    # ------------------------------------------------------------------ helpers

    def _client(self, config: dict[str, Any], secrets: dict[str, str]) -> httpx.Client:
        headers = {"Accept": "application/json"}
        auth = config.get("auth") if isinstance(config.get("auth"), dict) else {}
        token = secrets.get(str(auth.get("secret") or "api_key"))
        if token and auth.get("type") == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif token and auth.get("type") == "header" and auth.get("name"):
            headers[str(auth["name"])] = token
        return httpx.Client(timeout=TIMEOUT_SECONDS, transport=self._transport, follow_redirects=True, headers=headers)

    @staticmethod
    def _body(response: httpx.Response) -> tuple[Any, str]:
        text = response.text[:400_000]
        if "json" in response.headers.get("content-type", ""):
            try:
                return response.json(), text
            except ValueError:
                return text, text
        return text, text

    @staticmethod
    def _said(op: dict[str, Any], payload: Any) -> str:
        """One clause about what this step produced, in the service's words."""
        what = str(op.get("summary") or op.get("operation_id") or "").rstrip(".")
        if isinstance(payload, list):
            return f"{what}: {len(payload)} found."
        if isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, list):
                    return f"{what}: {len(value)} found."
        return f"{what}." if what else ""

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        try:
            body = response.json()
            if isinstance(body, dict):
                for key in ("detail", "message", "error"):
                    if isinstance(body.get(key), str):
                        return body[key][:400]
        except ValueError:
            pass
        return response.text[:400]

    @staticmethod
    def _failed(message: str, failure: FailureKind) -> InvocationResult:
        return InvocationResult(state=ResultState.FAILED, summary=message, error=message, failure=failure)


register_adapter(OpenApiAdapter())
