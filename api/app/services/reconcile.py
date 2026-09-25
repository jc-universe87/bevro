"""One place that decides what a provider currently is.

Bevro learned several things separately - that a folder needs permission,
that a credential can belong to one way in and not another, that a network
service may be visible only to the worker, that a name can be trimmed. Each
was an improvement, and each left behind a conclusion written down on the
provider at the time: *this* runtime is active, *this* credential is missing,
*this* connection is waiting. Conclusions go stale. Evidence does not.

So the rule is: persist evidence - the runtime profiles, what each can reach,
what each can get hold of, what the person has allowed, what recent runs said
- and work out the rest each time it is asked for. A provider that has not
been touched for a month shows what is true now, not what was true when it
was connected.

    reconcile(db, provider)   bring the written-down parts back in line with
                              the evidence: which runtime is active, whether
                              it is available, and nothing left over
    state_of(db, provider)    the derived picture the interface shows

`reconcile` is called after anything that could change the answer - Connect,
Reconnect, Test, a trust grant, a worker report - and is cheap: it looks at
nothing outside the database. Rediscovery, which is not cheap, happens only
when someone asks for it or when the discovery mechanism itself has moved on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from adapters.runtime import RuntimeProfile, credentials_label
from app.models import Provider
from app.services import runtime as runtime_service

log = logging.getLogger("bevro.reconcile")

# Bumped when discovery learns something that changes what it would find for
# a provider connected before. Providers below this are rediscovered once, by
# the worker, rather than being left to say what an older Bevro concluded.
#   2  credentials belong to runtimes; systemd environment files
#   3  execution contexts; installed template units; the installed unit's own ExecStart
#   4  what a credential held elsewhere is for; a program's own self-check option
DISCOVERY_VERSION = 4


@dataclass
class ProviderState:
    """What this provider is right now, worked out rather than remembered."""

    # The way in the execution engine would use for the next piece of work.
    selected: RuntimeProfile | None = None
    others: list[RuntimeProfile] = field(default_factory=list)
    # ready | paused | waiting_for_worker | needs_start | needs_credential |
    # unreachable | nothing_usable
    connection: str = "ready"
    note: str | None = None

    @property
    def available(self) -> bool:
        return self.connection == "ready"


# What each state is called in front of a person. One sentence, no machinery.
CONNECTION_WORDS = {
    "ready": "Ready",
    "paused": "Paused",
    "waiting_for_worker": "Waiting for the worker on this machine",
    # Something shipped with Bevro that nobody has set up. Not a fault, and
    # not worth saying in the language of a fault.
    "optional_worker": "Optional \u00b7 needs the host worker",
    "needs_start": "Not running",
    "needs_credential": "Needs a credential",
    "unreachable": "Can't be reached",
    "nothing_usable": "No way in that Bevro can use",
}


# --------------------------------------------------------------------------- the worker, said plainly

def worker_state(db) -> str:
    """running | absent.

    One question with one answer, so that nothing has to guess from the shape
    of something else. A worker says it is there; if none has said so lately,
    there is none.
    """
    from app.services import providers as provider_service

    return "running" if provider_service.worker_on_the_host(db) else "absent"


# --------------------------------------------------------------------------- deriving

def state_of(db, provider: Provider) -> ProviderState:
    """The current picture: which way in, and whether it can be used now."""
    # `explain=False`: what could actually take work now, with nothing put
    # forward merely to make a failure readable.
    usable, skipped = runtime_service.eligible_runtimes(provider, explain=False)
    every = runtime_service.runtimes_of(provider)
    selected = usable[0] if usable else runtime_service.active_runtime(provider)
    others = [rt for rt in every if selected is None or rt.id != selected.id]
    state = ProviderState(selected=selected, others=others)

    if not provider.enabled:
        state.connection = "paused"
    elif not usable:
        # Nothing here can take work. *Why* is the useful part, and the
        # reasons are already known - the worst of them is the answer.
        state.connection = _nothing_usable_because(skipped)
    elif selected.availability == "needs_start":
        state.connection = "needs_start"
    elif selected.credentials.required_from_user:
        state.connection = "needs_credential"
    else:
        state.connection = _reachable_now(db, provider, selected)
    state.note = CONNECTION_WORDS.get(state.connection)
    if state.connection == "ready":
        state.note = None  # nothing worth saying when nothing is wrong
    return state


def _nothing_usable_because(skipped: list[tuple[RuntimeProfile, str]]) -> str:
    """Why no way in can be used, in the order a person would care about."""
    reasons = {why for _rt, why in skipped}
    if "credential" in reasons:
        return "needs_credential"
    if "needs the worker on the host" in reasons:
        return "waiting_for_worker"
    if "cooling down after a failure" in reasons:
        return "unreachable"
    return "nothing_usable"


def _reachable_now(db, provider: Provider, selected: RuntimeProfile) -> str:
    """Can work actually start through this way in, right now?

    Four separate things, kept apart on purpose:

        is there a worker at all          its heartbeat says so
        does this way in need one         a program on this machine does
        can it be reached from there      recorded when something tried
        has anything been reached lately  the worker's own report

    "Waiting for the worker" means the first one is false. It must never mean
    merely that work happens on the worker, which is the ordinary case for
    everything local.
    """
    from app.services import providers as provider_service

    if runtime_service.execution_of(provider) != "background":
        return "ready"  # this process drives it; nothing to wait for
    if worker_state(db) == "absent":
        # A thing Bevro ships that nobody has set up is not waiting for
        # anything; it simply is not set up, and says so more kindly.
        never_reported = not (provider.availability or {})
        return "optional_worker" if provider.origin == "example" and never_reported else "waiting_for_worker"
    if runtime_service.is_network(selected) and selected.reachability.usable_from() == [] and selected.reachability.ruled_out(runtime_service.WORKER):
        return "unreachable"
    # A worker is here and this is its work. What it last said about the
    # provider refines that, but never contradicts it into "waiting".
    report = provider.availability or {}
    reported = str(report.get("state") or "")
    if reported in ("not_installed", "not_authenticated"):
        return "needs_credential" if reported == "not_authenticated" else "unreachable"
    if reported == "unavailable" and provider_service.report_is_fresh(report):
        return "unreachable"
    return "ready"


# --------------------------------------------------------------------------- can Bevro send it work

# Whether Bevro itself can send it work, as the interface says it. Only the
# first seven describe a way in that exists; "not_set_up" means there never
# was one, which is how a web app with no interface for programs simply is -
# neutral, and never shown as a fault.
DIRECT_STATES = ("ready", "needs_credential", "waiting_for_worker", "needs_start", "unreachable", "paused", "not_set_up")

DIRECT_WORDS = {
    "ready": "Bevro can send it work.",
    "needs_credential": "Bevro needs a credential before it can send it work.",
    "waiting_for_worker": "Bevro reaches it through the helper on this computer, which isn't running.",
    "needs_start": "It isn't running, so Bevro can't send it work right now.",
    "unreachable": "Bevro could send it work before, but can't reach it right now.",
    "paused": "Paused: Bevro won't send it work until you resume it.",
    "not_set_up": "Bevro can't send it work directly yet.",
}


def direct_access(db, provider: Provider) -> dict[str, Any]:
    """{"state", "note"}: can Bevro itself send this work, and if not, why.

    Kept apart from what the thing is and how the person uses it
    (docs/HUB.md). A way in that was found but has stopped working is a
    problem; no way in at all is not.
    """
    every = runtime_service.runtimes_of(provider)
    if not any(rt.invocable for rt in every):
        state = "not_set_up"
    else:
        connection = state_of(db, provider).connection
        state = {
            "optional_worker": "waiting_for_worker",
            "nothing_usable": "unreachable",
        }.get(connection, connection)
        if state not in DIRECT_STATES:
            state = "unreachable"
    return {"state": state, "note": DIRECT_WORDS[state]}


# --------------------------------------------------------------------------- bringing the record in line

def reconcile(db, provider: Provider, *, commit: bool = True) -> ProviderState:
    """Make the written-down parts agree with the evidence.

    Only three things are written: which way in is active (and the adapter
    block that follows it), the discovery version, and nothing else. Anything
    the interface shows is derived at the time it is asked for, so there is
    nothing left to go stale.
    """
    state = state_of(db, provider)
    if state.selected is not None and provider.active_runtime != state.selected.id:
        log.info("%s: now using %s rather than %s", provider.slug, state.selected.id, provider.active_runtime)
        runtime_service.set_runtimes(provider, runtime_service.runtimes_of(provider), state.selected.id)
    if provider.discovery_version != DISCOVERY_VERSION and _nothing_to_rediscover(provider):
        provider.discovery_version = DISCOVERY_VERSION
    if commit:
        db.commit()
    return state


def _nothing_to_rediscover(provider: Provider) -> bool:
    """Was this provider's evidence gathered by the current discovery?

    Anything Bevro cannot look at again - a service it was simply given an
    address for, its own built-ins - is as current as it will ever be.
    """
    return not (provider.source or {}).get("target")


def needs_rediscovery(provider: Provider) -> bool:
    """Was this connected before discovery learned what it now knows?"""
    return provider.origin == "connected" and (provider.discovery_version or 0) < DISCOVERY_VERSION and not _nothing_to_rediscover(provider)


def reconcile_all(db) -> int:
    """Every provider, after something that could have changed all of them."""
    from app.services.providers import list_providers

    for provider in list_providers(db, enabled_only=False):
        reconcile(db, provider, commit=False)
    db.commit()
    return 0


# --------------------------------------------------------------------------- what the interface shows

def credentials_view(state: ProviderState) -> dict[str, Any]:
    """One answer about credentials, from the way in that is actually used.

    Showing "uses its own" beside a runtime marked "managed by Bevro" is the
    sort of contradiction that comes of asking two different things. There is
    one question here: what does the selected way in need, and who has it.
    """
    selected = state.selected
    if selected is None:
        return {"label": "Unknown", "note": None, "names": []}
    creds = selected.credentials
    return {
        "label": credentials_label(creds),
        "note": creds.note,
        "names": list(creds.names),
        "status": creds.status,
        "owner": creds.owner,
    }
