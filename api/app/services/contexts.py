"""Execution contexts as the person sees them, and whether Bevro may use one.

What a context *is* was decided at discovery and is evidence (see
app/connect/contexts.py). Whether Bevro is *allowed* to use it is two
questions, answered now rather than remembered:

    does the machine let this user    recorded at discovery: PolicyKit, the
                                      socket's permissions, the Docker socket
    has the person said yes           a "context" trust grant, for exactly
                                      this context of this project, while
                                      the project itself is still allowed

Neither is ever asked in the ordinary course of Connect. Nothing runs through
a context yet, so there is nothing to approve; the grant exists so that when
something does, revoking it works like every other grant.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from adapters.runtime import CONTEXT_PROBLEMS, ContextKind, ExecutionContext, RuntimeProfile

TITLES = {
    ContextKind.SYSTEMD_SERVICE: "Installed system service",
    ContextKind.SYSTEMD_SOCKET: "System service that takes requests on a socket",
    ContextKind.COMPOSE_RUN: "Compose service",
}


def authorisation(db: Session | None, ctx: ExecutionContext) -> str:
    """authorised | needs_approval | not_permitted | unknown."""
    from app.services import trust as trust_service

    if ctx.privilege == "needs_admin":
        return "not_permitted"
    if ctx.privilege != "same_user" or db is None:
        return "unknown"
    return "authorised" if trust_service.allows_context(db, ctx.key) else "needs_approval"


def approve(db: Session, ctx: ExecutionContext):
    """Record the person's yes to exactly this context. Caller commits."""
    from app.services import trust as trust_service

    return trust_service.grant(db, trust_service.CONTEXT, ctx.key, label=f"{TITLES.get(ctx.kind, 'Context')}: {_name(ctx)}")


def _name(ctx: ExecutionContext) -> str:
    if ctx.kind == ContextKind.COMPOSE_RUN:
        file, _, service = ctx.source_ref.rpartition(":")
        return f"{service} ({file})"
    return ctx.source_ref


def describe(db: Session | None, rt: RuntimeProfile) -> dict[str, Any] | None:
    """One context, for Advanced details. Names of credentials; never values,
    paths or the command it runs."""
    ctx = rt.context
    if ctx is None:
        return None
    needs = list(rt.credentials.names)
    if not needs:
        credentials = "None needed"
    elif ctx.supplies:
        lacking = [n for n in needs if n not in ctx.supplies]
        credentials = f"Provides {', '.join(ctx.supplies)}" + (f" · not {', '.join(lacking)}" if lacking else "")
    else:
        credentials = "Provides none of what it needs"
    if ctx.launchable:
        takes_work = "Yes"
    elif ctx.compatible:
        # True, and worth saying precisely: nothing is wrong with it, Bevro
        # just doesn't run work this way yet.
        takes_work = "Could, in principle · Bevro doesn't use it yet"
    else:
        takes_work = "No"
    allowed = authorisation(db, ctx)
    return {
        "id": rt.id,
        "title": TITLES.get(ctx.kind, "Execution context"),
        "name": _name(ctx),
        "credentials": credentials,
        "takes_work": takes_work,
        "needs_admin": {"needs_admin": "Yes", "same_user": "No"}.get(ctx.privilege, "Unknown"),
        "authorised": {"authorised": "Yes", "needs_approval": "Not yet approved", "not_permitted": "No"}.get(allowed, "Unknown"),
        "available": ctx.available,
        "why_not": [CONTEXT_PROBLEMS[p] for p in ctx.problems if p in CONTEXT_PROBLEMS],
    }
