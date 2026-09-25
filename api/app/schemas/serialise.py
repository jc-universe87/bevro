"""ORM -> schema mapping in one place."""

from __future__ import annotations

import re
from typing import Any

from adapters.registry import adapter_kinds
from app.models import Artifact, Automation, Provider, ProviderRun, Task, Workspace
from app.schemas.automations import AutomationDetail, AutomationOut
from app.schemas.notifications import NotifyPreference
from app.schemas.providers import ProviderDetails, ProviderOut
from app.schemas.tasks import ArtifactOut, FailureAction, FailureOut, InputRequestOut, ProviderRef, RunOut, TaskDetail, TaskOut, WorkspaceRef
from app.services.artifacts import is_known_type
from app.services.providers import availability_of, credential_status, credentials_of, is_available, required_secrets, secret_label
from app.services.workspaces import permission_sentences


def provider_actions(provider: Provider, surfaces: list[Any] | None = None) -> list[str]:
    """Only what really works right now. No "Ask" for something nothing can run.

    "open" means there is a web app; whether the person's browser can open
    it from where it is is the browser's to work out (web/src/lib/hub.ts).
    """
    from app.connect.surfaces import for_provider
    from app.services.runtime import runtimes_of

    actions: list[str] = []
    kind = str(provider.adapter.get("kind", ""))
    invocable = any(rt.invocable for rt in runtimes_of(provider))
    if provider.enabled and invocable and kind in adapter_kinds() and is_available(provider):
        actions.append("ask")
    if any(s.kind == "web_app" for s in (surfaces if surfaces is not None else for_provider(provider.surfaces, provider.app_url))):
        actions.append("open")
    return actions


def connection_label(provider: Provider) -> str | None:
    method = provider.adapter.get("method")
    if method:
        return str(method)
    kind = provider.adapter.get("kind")
    if provider.origin == "example":
        return "built-in"
    if kind == "declared":
        return "declared"
    return str(kind) if kind else None


def runtime_summary(provider: Provider, stored: list[str], db: Any = None) -> dict[str, Any] | None:
    from adapters.runtime import credentials_label
    from app.services.runtime import active_runtime, runtimes_of

    if db is not None:
        from app.services.reconcile import state_of

        rt = state_of(db, provider).selected
    else:
        rt = active_runtime(provider)
    if rt is None:
        return None
    statuses = credential_status(provider, stored)
    if any(v == "missing" for v in statuses.values()):
        label = "Missing"
    elif any(v == "bevro" for v in statuses.values()):
        label = "Managed by Bevro"
    elif statuses:
        label = "Managed by provider"
    else:
        label = credentials_label(rt.credentials)
    from app.services.bridges import review_state

    others = [r for r in runtimes_of(provider) if r.id != rt.id and r.invocable]
    built = bool((rt.adapter.get("config") or {}).get("bridge"))
    return {
        "built": built,
        "review": review_state(provider) if built else None,
        "display_name": rt.display_name,
        # Where it is driven from, when that is not simply "here": a
        # connection only the host can reach needs the worker running.
        "runs_at": "On this machine" if rt.reachability.host_only() else None,
        "availability": rt.availability,
        "credentials_label": label,
        "runtimes_found": len(runtimes_of(provider)),
        "alternatives": len(others),
        "health": rt.health.state.value,
        "abilities": rt.abilities.model_dump(),
    }


def provider_copy(provider: Provider) -> dict[str, str]:
    """The plain-English layer: what it does, how it connects.

    Built from what the provider declares and the mechanism it is reached
    through - never from the text the service wrote about its own interface.
    """
    from app.connect import copy
    from app.services.runtime import active_runtime

    kind = str(provider.adapter.get("kind") or "")
    rt = active_runtime(provider)
    holds_credential = any(c.get("source") == "bevro" for c in credentials_of(provider, []))
    return {
        "what_it_does": copy.what_it_does(provider.name, provider.capabilities, kind=kind, stored=provider.description),
        "how_it_connects": copy.how_it_connects(
            kind=kind,
            host_only=bool(rt is not None and rt.reachability.host_only()),
            bevro_holds_credential=holds_credential,
        ),
    }


def provider_summary(provider: Provider) -> str:
    """The one sentence on the card. Never the service's own prose."""
    from app.connect import copy

    return copy.summary_for(provider.name, provider.capabilities, stored=provider.description)


def _source_target(provider: Provider) -> str | None:
    """The address this was connected from, as the person typed it.

    Addresses only. A folder is left out even though the person typed it,
    because a path describes the machine Bevro runs on and this view goes to
    a browser; "Connected from: a local folder" says enough. A command is
    left out because its arguments may carry a secret.
    """
    source = provider.source or {}
    target = str(source.get("target") or "")
    kind = str(source.get("target_kind") or "")
    if not target or (kind and kind not in ("url", "mcp")) or not re.match(r"^https?://", target):
        return None
    # An address may carry credentials in front of the host. Those are not
    # the person's input being echoed back; they are a secret.
    return re.sub(r"//[^/@\s]*@", "//", target)[:300]


def _location_class(provider: Provider) -> str | None:
    """What kind of address this turned out to be. Never asked, never acted
    on: a private address is as welcome as a public one. Worth being able to
    see when something is not behaving."""
    from app.connect.targets import TargetError, classify_target

    target = str((provider.source or {}).get("target") or "")
    if not target:
        return None
    try:
        return classify_target(target).location_class
    except TargetError:
        return None


def _connected_from(provider: Provider) -> str | None:
    """What the person connected from, not how discovery ended up reading it.

    Older records only kept the reading ("http", "mcp"), which is the runtime
    line's business; both of those were an address to whoever typed one.
    """
    source = provider.source or {}
    kind = str(source.get("target_kind") or source.get("kind") or "")
    return {"http": "url", "openapi": "url"}.get(kind, kind) or None


def _runtime_row(rt: Any, *, selected: bool) -> dict[str, Any]:
    """One way in, as Advanced details lists it.

    The credential line says what is actually true of *this* way in - what it
    needs, and who has it - rather than the strategy it was built with. A
    command line marked "managed by Bevro" that Bevro is not managing
    anything for is the sort of thing that makes a page contradict itself.
    """
    creds = rt.credentials
    return {
        **rt.advanced(),
        "display_name": rt.display_name,
        "availability": rt.availability,
        "active": selected,
        "credential_summary": _credential_line(creds),
        "reachable_from": _reached_from(rt),
        "usable": rt.invocable,
        "why_not": (rt.warnings[0] if rt.warnings else None) if not rt.invocable else None,
    }


def _credential_line(creds: Any) -> str:
    if not creds.names:
        return "None needed"
    if creds.status == "configured":
        return f"{', '.join(creds.names)} · provided by this way in"
    if creds.owner == "other_runtime":
        return f"{', '.join(creds.missing)} · another way in has it, this one doesn't"
    if creds.owner == "bevro":
        return f"{', '.join(creds.missing)} · Bevro would have to supply it"
    return f"{', '.join(creds.missing)} · nothing here has it"


def _reached_from(rt: Any) -> str | None:
    from app.services.runtime import is_network

    if not is_network(rt):
        return None
    reach = rt.reachability
    return f"Bevro itself: {reach.api} · this machine: {reach.worker}"


def provider_details(provider: Provider, db: Any = None) -> ProviderDetails:
    from app.connect.openapi import catalogue_from_json
    from app.services.contexts import describe as describe_context
    from app.services.runtime import active_runtime, runtimes_of

    if db is not None:
        from app.services.reconcile import state_of

        rt = state_of(db, provider).selected
    else:
        rt = active_runtime(provider)
    config = provider.adapter.get("config") or {}
    operations = catalogue_from_json(config.get("operations")) if config.get("operations") else []
    return ProviderDetails(
        id=provider.id,
        active_runtime=rt.advanced() if rt else None,
        runtimes=[_runtime_row(r, selected=rt is not None and r.id == rt.id) for r in runtimes_of(provider)],
        source_kind=_connected_from(provider),
        source_name=provider.source_name if provider.source_name and provider.source_name != provider.name else None,
        location_class=_location_class(provider),
        source_target=_source_target(provider),
        source_description=provider.source_description or None,
        operation_count=len(operations) or None,
        runs_at="This machine" if rt is not None and rt.reachability.host_only() else ("Bevro itself" if rt is not None else None),
        reachability=rt.reachability.model_dump(mode="json") if rt is not None else None,
        contexts=[view for view in (describe_context(db, r) for r in runtimes_of(provider)) if view is not None],
    )


def provider_out(provider: Provider, secret_names: list[str] | None = None, db: Any = None) -> ProviderOut:
    stored = secret_names or []
    build = None
    if provider.origin == "created" and db is not None:
        from app.services.agents import summary

        build = summary(db, provider)
    from app.connect.surfaces import for_provider
    from app.connect.surfaces import public as surface_public

    surfaces = for_provider(provider.surfaces, provider.app_url)
    return ProviderOut(
        id=provider.id,
        slug=provider.slug,
        name=provider.name,
        description=provider_summary(provider),
        details=provider_copy(provider),
        enabled=provider.enabled,
        capabilities=provider.capabilities,
        app_url=provider.app_url,
        icon=provider.icon,
        origin=provider.origin,
        actions=provider_actions(provider, surfaces),
        connection=connection_label(provider),
        availability=availability_of(provider, db),
        surfaces=[surface_public(x) for x in surfaces],
        direct=_direct(provider, db),
        secret_names=stored,
        credentials=credentials_of(provider, stored),
        runtime=runtime_summary(provider, stored, db),
        build=build,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _direct(provider: Provider, db: Any) -> dict[str, Any]:
    if db is None:
        from app.services.runtime import runtimes_of

        # No session: say only what is known without one.
        return {"state": "not_set_up" if not any(rt.invocable for rt in runtimes_of(provider)) else ("paused" if not provider.enabled else "ready")}
    from app.services.reconcile import direct_access

    return direct_access(db, provider)


def provider_ref(provider: Provider) -> ProviderRef:
    return ProviderRef(id=provider.id, slug=provider.slug, name=provider.name)


def run_provider_ref(run: ProviderRun) -> ProviderRef:
    """Who did this work, whether or not they are still here.

    History does not change when an agent is renamed or removed: the name
    stored on the run at the time is what a person is shown.
    """
    if run.provider is not None:
        return provider_ref(run.provider)
    return ProviderRef(id=None, slug=run.provider_slug, name=run.provider_name or "Removed agent", removed=True)


FAILURE_TITLES = {
    "configuration_problem": "Needs setting up again",
    "credential_required": "Needs a credential",
    "provider_unavailable": "Couldn't be reached",
    "invocation_failed": "Didn't finish",
    "timed_out": "Took too long",
    "cancelled": "Stopped",
    "output_invalid": "Unreadable answer",
}
# Values written before the kinds were renamed.
_LEGACY_FAILURES = {"execution_failed": "invocation_failed"}


def failure_out(run: ProviderRun, stored_secret_names: list[str] | None = None) -> FailureOut | None:
    """The person-facing failure: category, sentence, next steps. Nothing technical."""
    if run.state != "failed":
        return None
    provider = run.provider
    name = provider.name if provider is not None else (run.provider_name or "That agent")
    if provider is None:
        # The agent has since been removed. The record of why this failed is
        # still worth reading; there is simply nothing left to act on.
        return FailureOut(
            category="provider_unavailable",
            title="Agent removed",
            message=f"{name} was removed from Bevro after this ran.",
            actions=[],
        )
    category = str((run.meta or {}).get("failure") or "")
    category = _LEGACY_FAILURES.get(category, category)
    missing = [n for n, source in credential_status(provider, stored_secret_names or []).items() if source == "missing"]
    if missing:
        # Whatever the run said, a credential the connection needs is not there. Say that first.
        category = "credential_required"
    if category not in FAILURE_TITLES:
        category = "invocation_failed"
    retry = FailureAction(kind="retry", label="Try again")
    manage = FailureAction(kind="manage", label=f"Go to {name}")
    if category == "credential_required":
        target = missing[0] if missing else (required_secrets(provider) or ["api_key"])[0]
        label = secret_label(target)
        verb = "needs" if missing else "couldn't use"
        article = "an" if label[:1].lower() in "aeiou" else "a"
        message = f"{name} {verb} {article} {label} before it can run." if missing else f"{name} {verb} its {label}. Check it and try again."
        actions = [FailureAction(kind="add_credential", label="Add credential" if missing else "Update credential", secret_name=target, secret_label=label), retry]
    elif category == "provider_unavailable":
        message = f"{name} isn't available right now."
        actions = [FailureAction(kind="test_connection", label=f"Check {name}"), retry]
    elif category == "configuration_problem":
        message = f"Bevro couldn't start {name} with its current connection."
        actions = [FailureAction(kind="manage", label=f"Go to {name}")]
    elif category == "timed_out":
        message = f"{name} took too long and was stopped."
        actions = [retry, manage]
    elif category == "cancelled":
        message = "This task was stopped before it finished."
        actions = [retry]
    elif category == "output_invalid":
        message = f"{name} answered, but Bevro couldn't read the result."
        actions = [retry, manage]
    else:
        message = f"{name} started but couldn't finish this task."
        actions = [retry, manage]
    return FailureOut(category=category, title=FAILURE_TITLES[category], message=message, actions=actions)


def recovered_after_fallback(run: ProviderRun) -> bool:
    """Did this run need more than one way in before it worked?"""
    if run.state != "completed":
        return False
    attempts = (run.meta or {}).get("attempts") or []
    return sum(1 for a in attempts if isinstance(a, dict) and a.get("outcome") != "skipped") > 1


def run_out(run: ProviderRun, workspaces: dict[str, Workspace] | None = None, stored_secret_names: list[str] | None = None) -> RunOut:
    progress = run.progress or {}
    workspace = None
    permissions: list[str] = []
    ws_id = run.input.get("workspace_id") if run.input else None
    if ws_id and workspaces and ws_id in workspaces:
        ws = workspaces[ws_id]
        workspace = WorkspaceRef(id=ws.id, name=ws.name)
        permissions = permission_sentences(list(run.input.get("permissions") or ws.permissions), ws.name)
    return RunOut(
        id=run.id,
        provider=run_provider_ref(run),
        state=run.state,
        result_summary=run.result_summary,
        error_summary=run.error_summary,
        failure=failure_out(run, stored_secret_names),
        recovered=recovered_after_fallback(run),
        phase=progress.get("phase") if run.state == "running" else None,
        steps=[str(step) for step in progress.get("steps") or []],
        workspace=workspace,
        permissions=permissions,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def input_request_out(task: Task) -> InputRequestOut | None:
    if task.state != "needs_input":
        return None
    for run in reversed(task.runs):
        if run.input_request:
            req = run.input_request
            return InputRequestOut(
                question=str(req.get("question", "")),
                kind=str(req.get("kind", "choice")),
                options=[{"value": str(o.get("value")), "label": str(o.get("label"))} for o in req.get("options", [])],
            )
    return None


def artifact_out(artifact: Artifact) -> ArtifactOut:
    return ArtifactOut(
        id=artifact.id,
        task_id=artifact.task_id,
        provider_run_id=artifact.provider_run_id,
        type=artifact.type,
        title=artifact.title,
        summary=artifact.summary,
        mime_type=artifact.mime_type,
        payload=artifact.payload,
        external_url=artifact.external_url,
        content_url=f"/api/artifacts/{artifact.id}/content" if artifact.storage_path else None,
        metadata=artifact.meta,
        known=is_known_type(artifact.type),
        created_at=artifact.created_at,
    )


def _primary_provider(task: Task) -> ProviderRef | None:
    if not task.runs:
        return None
    return run_provider_ref(task.runs[-1])


def task_out(task: Task) -> TaskOut:
    return TaskOut(
        id=task.id,
        title=task.title,
        original_request=task.original_request,
        state=task.state,
        summary=task.summary,
        provider=_primary_provider(task),
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
    )


def task_detail(task: Task, workspaces: dict[str, Workspace] | None = None, secret_names: dict[str, list[str]] | None = None) -> TaskDetail:
    """`secret_names` maps provider id -> names of stored secrets (never values), so a
    failed run can say whether a credential is missing."""
    base = task_out(task)
    return TaskDetail(
        **base.model_dump(),
        runs=[run_out(r, workspaces, (secret_names or {}).get(str(r.provider_id))) for r in task.runs],
        artifacts=[artifact_out(a) for a in task.artifacts],
        input_request=input_request_out(task),
    )


# --------------------------------------------------------------------------- automations

def _last_result_words(db, automation: Automation) -> str | None:
    """How the last turn went, in one plain line."""
    from app.services.automations import last_run

    run = last_run(db, automation)
    if run is None:
        return None
    if run.outcome == "failed":
        return run.reason or "Didn't finish last time."
    if run.outcome == "running":
        return "Running now"
    if automation.mode == "monitoring":
        return run.reason or ("Something to see" if run.matched else "No change last run")
    return f"Ran {relative_day(run.scheduled_for)}" if run.scheduled_for else "Ran"


def relative_day(moment) -> str:
    from app.models._common import utcnow

    days = (utcnow().date() - moment.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"on {moment:%A}"
    return f"on {moment:%-d %b}"


def automation_out(db, automation: Automation) -> AutomationOut:
    from app.services import notifications as notification_service
    from app.services.automations import condition_of, spec_of

    spec = spec_of(automation)
    return AutomationOut(
        id=automation.id,
        title=automation.title,
        instruction=automation.instruction,
        mode=automation.mode,
        enabled=automation.enabled,
        schedule=spec.describe(),
        condition=condition_of(automation).describe() if automation.mode == "monitoring" else None,
        provider=provider_ref(automation.provider) if automation.provider is not None else None,
        next_run_at=automation.next_run_at if automation.enabled else None,
        last_run_at=automation.last_run_at,
        last_result=_last_result_words(db, automation),
        notify=NotifyPreference(**notification_service.preferences(automation.notify)),
        created_at=automation.created_at,
    )


def automation_detail(db, automation: Automation) -> AutomationDetail:
    """With its history. Kept for Details: times, outcomes, and why."""
    from app.services.automations import history, spec_of

    base = automation_out(db, automation)
    spec = spec_of(automation)
    return AutomationDetail(
        **base.model_dump(),
        runs=[
            {
                "id": str(run.id),
                "task_id": str(run.task_id) if run.task_id else None,
                "scheduled_for": run.scheduled_for.isoformat(),
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "delayed": run.delayed,
                "trigger": run.trigger,
                "outcome": run.outcome,
                "matched": run.matched,
                "reason": run.reason,
            }
            for run in history(db, automation)
        ],
        recurrence=spec.canonical(),
        timezone=spec.timezone,
        selection=automation.selection,
    )
