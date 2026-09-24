"""What a person reads about a provider, and what is kept out of their way.

A service's own description is written for whoever integrates with it. Bevro
reads it, keeps it, and does not put it on a card. These tests are mostly
about that line: summary and details are built from facts; the prose the
service wrote stays under Advanced details where it is a useful technical
fact rather than an answer to "what is this for".

Every case here uses a generic fixture. Nothing knows what any particular
service is, and there is no per-provider wording anywhere to test.
"""

from __future__ import annotations

import json

import pytest

from app.connect import copy
from app.schemas.serialise import provider_details, provider_out
from app.services import providers as provider_service
from tests import operational_fixtures as fx

# What a service that documents its own interface sounds like. Every phrase
# here is the sort of thing that is true, useful to a developer, and wrong on
# a card.
INTEGRATION_PROSE = (
    "Thin control API over the Example modules. It exposes existing capabilities (profiles, strategies, "
    "runs, decisions, preparation, review, tracking, artifacts, statistics); it implements no search, "
    "evaluation, matching, wording, approval or state-transition logic of its own. GET routes read. "
    "POST routes are explicit operations. No authentication (the network is the intended boundary), no "
    "database. Files are editable through the workspace routes: narrow, validated writes to the "
    "profile's own files, never arbitrary paths."
)

CAPABILITIES = [
    {"id": "documents", "title": "Documents"},
    {"id": "applications", "title": "Applications"},
    {"id": "vacancies", "title": "Vacancies"},
    {"id": "shortlist", "title": "Shortlist"},
    {"id": "profiles", "title": "Profiles"},
    {"id": "artifacts", "title": "Artifacts"},
]

# Words that belong to the machinery. None of them may appear in copy a
# person reads, whichever mechanism the provider is reached through.
TECHNICAL_WORDS = (
    "openapi", "swagger", "endpoint", "operation id", "schema", "descriptor", "payload",
    "adapter", "runtime", "invocation", "json", "http", "get ", "post ", "/api/", "`",
)


def a_provider(db, *, kind: str, description: str, capabilities=None, origin: str = "connected"):
    return provider_service.register_provider(
        db,
        {
            "name": "Example Service",
            "description": description,
            "capabilities": capabilities if capabilities is not None else CAPABILITIES,
            "adapter": {"kind": kind, "config": {"base_url": "http://service.local"}},
            "origin": origin,
            "source": {"kind": "url", "target_kind": "url", "target": "http://service.local/p/someone/"},
        },
    )


# --------------------------------------------------------------------------- the line itself

def test_a_service_s_own_integration_prose_never_becomes_the_description(seeded):
    """The whole point. What the service wrote is evidence; it is kept, and it
    is not what someone sees when they look at their agents."""
    provider = a_provider(seeded, kind="openapi", description=INTEGRATION_PROSE)
    seeded.commit()

    card = provider_out(provider)
    assert card.description != INTEGRATION_PROSE
    assert len(card.description) <= copy.MAX_SUMMARY
    assert INTEGRATION_PROSE[:60] not in json.dumps(card.model_dump(mode="json"))


def test_the_original_is_kept_and_shown_where_it_belongs(seeded):
    """Nothing is thrown away: Advanced details is where a technical fact is
    the thing that is wanted."""
    provider = a_provider(seeded, kind="openapi", description=INTEGRATION_PROSE)
    provider_service.settle_descriptions(seeded)
    seeded.refresh(provider)

    assert provider.source_description == INTEGRATION_PROSE
    assert provider_details(provider).source_description == INTEGRATION_PROSE
    assert provider.description != INTEGRATION_PROSE


def test_settling_twice_does_not_lose_the_original(seeded):
    provider = a_provider(seeded, kind="openapi", description=INTEGRATION_PROSE)
    provider_service.settle_descriptions(seeded)
    provider_service.settle_descriptions(seeded)
    seeded.refresh(provider)
    assert provider.source_description == INTEGRATION_PROSE


# --------------------------------------------------------------------------- plain English

@pytest.mark.parametrize("kind", ["openapi", "http", "mcp", "command", "claude_code"])
def test_no_mechanism_leaks_technical_words_into_what_a_person_reads(seeded, kind):
    """The same description system for every way in, and none of them may
    describe themselves in the words Bevro uses internally."""
    provider = a_provider(seeded, kind=kind, description=INTEGRATION_PROSE)
    seeded.commit()
    card = provider_out(provider)

    readable = " ".join([card.description, card.details["what_it_does"], card.details["how_it_connects"]]).lower()
    for word in TECHNICAL_WORDS:
        assert word not in readable, (kind, word)
    assert card.details["what_it_does"] and card.details["how_it_connects"]


def test_a_summary_stays_short_however_much_there_is_to_say(seeded):
    many = [{"id": f"thing_{n}", "title": f"Something rather long number {n}"} for n in range(20)]
    provider = a_provider(seeded, kind="openapi", description="", capabilities=many)
    seeded.commit()
    assert len(provider_out(provider).description) <= copy.MAX_SUMMARY


def test_capabilities_become_a_sentence_rather_than_a_list(seeded):
    provider = a_provider(seeded, kind="openapi", description=INTEGRATION_PROSE)
    seeded.commit()
    summary = provider_out(provider).description
    assert summary.endswith(".")
    assert " and " in summary  # a sentence, not "documents, applications, vacancies"
    assert "documents" in summary


def test_the_machinery_is_left_out_when_there_is_real_work_to_name(seeded):
    """"Profiles" and "artifacts" are how a thing is built; "applications" is
    what someone wanted. Both are the service's own words, and the summary
    has room for four."""
    provider = a_provider(seeded, kind="openapi", description="")
    seeded.commit()
    summary = provider_out(provider).description
    assert "artifacts" not in summary and "profiles" not in summary
    assert "applications" in summary


def test_a_provider_that_says_nothing_about_itself_still_says_something(seeded):
    provider = a_provider(seeded, kind="http", description="", capabilities=[])
    seeded.commit()
    card = provider_out(provider)
    assert card.description == copy.NOTHING_KNOWN
    # And it says where the technical text went, rather than pretending.
    assert "Advanced details" in card.details["what_it_does"]
    assert card.details["how_it_connects"].startswith("Bevro connects directly to the service")


def test_copy_someone_wrote_is_theirs_and_is_kept(seeded):
    """Bevro's own built-ins, an agent it was asked to build, a sentence the
    person typed: short and plain already, and not Bevro's to rewrite."""
    provider = a_provider(seeded, kind="claude_code", description="Build, fix and change software", origin="example")
    seeded.commit()
    assert provider_out(provider).description == "Build, fix and change software"
    assert provider_service.settle_descriptions(seeded) == 0


# --------------------------------------------------------------------------- advanced details

def test_advanced_details_still_answers_the_technical_questions(seeded):
    from app.services import connect as connect_service
    from app.connect.service import ConnectionDiscoveryService, set_discovery_service
    from app.connect.strategies.command import CommandStrategy
    from app.connect.strategies.http import HttpDiscoveryStrategy
    from app.connect.strategies.local import LocalProjectStrategy

    transport, _calls = fx.service(descriptor=fx.JOBS_API)
    set_discovery_service(ConnectionDiscoveryService([HttpDiscoveryStrategy(transport), LocalProjectStrategy(), CommandStrategy()], use_assist=False))
    try:
        row = connect_service.start_discovery(seeded, "http://service.local/")
        provider = connect_service.confirm_draft(seeded, row, name=None, description=None, capability_summary=None, secrets={}, app_url=None)
    finally:
        set_discovery_service(None)

    details = provider_details(provider)
    assert details.active_runtime and details.active_runtime["kind"] == "openapi"
    assert details.operation_count == 5
    assert details.source_target == "http://service.local/"
    assert details.runs_at in ("Bevro itself", "This machine")
    assert details.reachability is not None


def test_advanced_details_gives_back_an_address_but_never_a_path_or_a_command(seeded):
    """The person's own address, echoed back when something is wrong. A path
    describes this machine and a command line may carry a secret."""
    for target_kind, target in (("local", "/home/someone/agents/thing"), ("command", "python -m thing --key hunter2")):
        provider = a_provider(seeded, kind="command", description="Runs a thing")
        provider.source = {"kind": target_kind, "target_kind": target_kind, "target": target}
        seeded.commit()
        assert provider_details(provider).source_target is None

    provider = a_provider(seeded, kind="http", description="Answers questions")
    provider.source = {"kind": "url", "target_kind": "url", "target": "https://user:hunter2@service.local/x"}
    seeded.commit()
    given_back = provider_details(provider).source_target
    assert given_back == "https://service.local/x"
    assert "hunter2" not in str(given_back) and "user" not in str(given_back)


# --------------------------------------------------------------------------- what "fit to show" means

@pytest.mark.parametrize(
    "text",
    [
        INTEGRATION_PROSE,
        "GET /api/v1/things returns a list.",
        "An OpenAPI service.",
        "Exposes endpoints for widgets.",
        "Returns JSON over HTTP.",
        "x" * 400,
        "",
        None,
    ],
)
def test_text_written_for_an_integrator_is_not_offered_to_a_person(text):
    assert copy.is_fit_to_show(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "Build, fix and change software",
        "Answers questions about quotes.",
        "Finds suitable roles and helps you keep track of them.",
        "Keeps an eye on competitor pricing and tells you what changed.",
    ],
)
def test_a_sentence_written_for_a_person_is_left_alone(text):
    assert copy.is_fit_to_show(text) is True


# --------------------------------------------------------------------------- services that have nothing to do with each other
#
# The point of these is that none of them is the service this work was
# prompted by. If the wording only reads well for one product, it is an
# integration wearing generic clothes, and one of these will show it.

def _spec(title: str, description: str, operations: list[tuple[str, str, str, str]]) -> dict:
    """A descriptor from (method, path, tag, summary) rows - nothing else needed."""
    paths: dict[str, dict] = {}
    for method, path, tag, summary in operations:
        paths.setdefault(path, {})[method.lower()] = {
            "summary": summary,
            "tags": [tag],
            "operationId": f"{method.lower()}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}",
            "responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}},
        }
    return {"openapi": "3.1.0", "info": {"title": title, "description": description}, "paths": paths}


CONVERTER = _spec(
    "Docsmith REST API",
    "Endpoints for document conversion. POST /convert accepts multipart uploads; see the OpenAPI schema.",
    [
        ("get", "/api/v1/documents", "documents", "Every document, newest first"),
        ("post", "/api/v1/documents", "documents", "Upload a document"),
        ("post", "/api/v1/documents/{document_id}/convert", "documents", "Convert the document to another format"),
        ("post", "/api/v1/documents/{document_id}/render", "documents", "Render the document as a PDF"),
        ("get", "/api/v1/health", "health", "Liveness"),
    ],
)

MONITOR = _spec(
    "Sentrywatch Service API",
    "HTTP API exposing monitor CRUD and incident retrieval. All responses are JSON.",
    [
        ("get", "/api/v1/monitors", "monitors", "Every monitor and its current state"),
        ("post", "/api/v1/monitors", "monitors", "Create a monitor"),
        ("post", "/api/v1/monitors/{monitor_id}/check", "monitors", "Run the check now"),
        ("get", "/api/v1/incidents", "incidents", "Open incidents, newest first"),
        ("post", "/api/v1/incidents/{incident_id}/acknowledge", "incidents", "Acknowledge the incident"),
        ("get", "/api/v1/incidents/stats", "incidents", "Counts by severity"),
        ("get", "/api/v1/settings", "settings", "Current configuration"),
    ],
)

TRACKER = _spec(
    "Planwell control API",
    "Thin control API over the Planwell core. GET routes read; POST routes mutate. No authentication.",
    [
        ("get", "/api/v1/projects", "projects", "Every project"),
        ("post", "/api/v1/projects", "projects", "Create a project"),
        ("patch", "/api/v1/projects/{project_id}", "projects", "Update the project"),
        ("get", "/api/v1/tasks", "tasks", "Every task"),
        ("post", "/api/v1/tasks", "tasks", "Create a task"),
        ("patch", "/api/v1/tasks/{task_id}", "tasks", "Update the task"),
        ("get", "/api/v1/profiles", "profiles", "Every profile"),
        ("get", "/api/v1/workspace/files", "workspace", "Files in the workspace"),
        ("put", "/api/v1/workspace/files/{name}", "workspace", "Replace a file in the workspace"),
        ("get", "/api/v1/health", "health", "Liveness"),
    ],
)


def _capabilities_of(spec: dict) -> list[dict]:
    from app.connect.openapi import capabilities_from_operations, compile_catalogue

    return capabilities_from_operations(compile_catalogue(spec))


def _summary_of(spec: dict) -> str:
    return copy.summary_for(copy.display_name(spec["info"]["title"]), _capabilities_of(spec), stored=None)


def test_a_document_service_is_described_as_working_with_documents():
    """A: nothing to do with anything else here, and it should say so."""
    summary = _summary_of(CONVERTER)
    assert "document" in summary.lower()
    assert any(verb in summary.lower() for verb in ("convert", "prepare", "create")), summary
    assert "health" not in summary.lower()
    assert len(summary) <= copy.MAX_SUMMARY


def test_a_monitoring_service_is_described_as_watching_things():
    """B."""
    summary = _summary_of(MONITOR)
    assert "monitor" in summary.lower() or "incident" in summary.lower()
    assert "settings" not in summary.lower()
    assert len(summary) <= copy.MAX_SUMMARY


def test_a_project_service_is_described_as_working_with_tasks_and_projects():
    """C."""
    summary = _summary_of(TRACKER)
    assert "task" in summary.lower() and "project" in summary.lower()
    assert any(verb in summary.lower() for verb in ("create", "manage", "update")), summary


def test_plumbing_does_not_get_to_dominate_a_summary():
    """D: profiles, workspace and health are how a thing is built. The two
    groups someone actually came for are the ones that get said."""
    summary = _summary_of(TRACKER).lower()
    for plumbing in ("profile", "workspace", "health"):
        assert plumbing not in summary, summary
    # ...and nothing is thrown away: they are still there to look at.
    assert {"profiles", "workspace", "health"} & {c["id"] for c in _capabilities_of(TRACKER)}


@pytest.mark.parametrize(
    ("given", "shown"),
    [
        ("Career Agent control API", "Career Agent"),
        ("Inventory REST API", "Inventory"),
        ("Docsmith REST API", "Docsmith"),
        ("Planwell control API", "Planwell"),
        ("Example control API", "Example"),
        ("Billing HTTP API", "Billing"),
        ("Payments API v2", "Payments"),
        ("Orders OpenAPI", "Orders"),
        # Left alone: a word about the thing, not about its wiring.
        ("Acme Search Service", "Acme Search Service"),
        ("Notes", "Notes"),
        ("Claude Code", "Claude Code"),
        # Nothing else to call it by.
        ("API", "API"),
    ],
)
def test_a_name_loses_the_part_that_describes_its_own_plumbing(given, shown):
    """E."""
    assert copy.display_name(given) == shown


def test_a_good_description_someone_wrote_is_left_exactly_as_it_is(seeded):
    """F: Bevro rewrites integration documents, not other people's writing."""
    written = "Finds suitable roles and keeps track of where each application has got to."
    provider = a_provider(seeded, kind="openapi", description=written)
    seeded.commit()
    assert provider_out(provider).description == written
    assert provider_service.settle_descriptions(seeded) == 0


# --------------------------------------------------------------------------- the shape of the sentence

def test_a_summary_says_what_it_does_rather_than_what_it_has():
    """The difference between "helps with documents" and "prepares documents"
    is the difference between a noun someone's code uses and an answer."""
    for spec in (CONVERTER, MONITOR, TRACKER):
        summary = _summary_of(spec)
        assert not summary.lower().startswith("helps with"), summary
        assert summary.endswith(".") and summary[0].isupper()


def test_every_capability_says_what_it_does_too():
    for capability in _capabilities_of(TRACKER):
        assert capability["title"][0].isupper()
        assert " " in capability["action"], capability
        assert capability["verb"] and capability["subject"]


@pytest.mark.parametrize("spec", [CONVERTER, MONITOR, TRACKER])
def test_no_summary_reads_like_the_document_it_came_from(spec):
    summary = _summary_of(spec)
    assert copy.is_fit_to_show(summary), summary
    for word in ("api", "endpoint", "openapi", "get ", "post ", "json", "crud"):
        assert word not in summary.lower(), (spec["info"]["title"], word)


# --------------------------------------------------------------------------- saying a word twice
#
# "Tracks monitors" is not a description. Neither is "manages management" or
# "runs runs": the word that named the thing has been reused as the word for
# doing something to it, and the phrase says nothing twice. These are the
# shapes that produces, each built from an ordinary service.

def _group(tag: str, operations: list[tuple[str, str, str]]) -> list[dict]:
    """One group of operations as the phrasing engine sees it."""
    return [
        {"method": method, "path": path, "summary": summary, "safety": "state_change" if method == "post" else "read_only"}
        for method, path, summary in operations
    ]


COLLISIONS = [
    (
        "monitors",
        [("get", "/api/v1/monitors", "Every monitor and its state"), ("post", "/api/v1/monitors", "Create a monitor"), ("post", "/api/v1/monitors/{id}/check", "Run the check now")],
    ),
    (
        "management",
        [("get", "/api/v1/management", "Management overview"), ("post", "/api/v1/management/{id}", "Manage the entry")],
    ),
    (
        "reports",
        [("get", "/api/v1/reports", "Every report"), ("post", "/api/v1/reports", "Report on the period")],
    ),
    (
        "runs",
        [("get", "/api/v1/runs", "Runs, newest first"), ("post", "/api/v1/runs/{id}/execute", "Run the pending stages")],
    ),
    (
        "processes",
        [("get", "/api/v1/processes", "Every process"), ("post", "/api/v1/processes/{id}", "Process the item")],
    ),
    (
        "searches",
        [("get", "/api/v1/searches", "Every search"), ("post", "/api/v1/searches", "Search for matches")],
    ),
    (
        "tracking",
        [("get", "/api/v1/tracking", "Tracking state"), ("post", "/api/v1/tracking/{id}", "Track the item")],
    ),
    # Nothing here says what any of it is for, so the method is the only
    # evidence there is - and it must not be allowed to say the word twice.
    ("creations", [("post", "/api/v1/creations", "Create a creation")]),
    ("listings", [("get", "/api/v1/listings", "Every listing"), ("get", "/api/v1/listings/{id}", "List one")]),
    ("executions", [("get", "/api/v1/executions", "Every execution"), ("post", "/api/v1/executions", "Execute it")]),
]


@pytest.mark.parametrize(("tag", "operations"), COLLISIONS, ids=[tag for tag, _ops in COLLISIONS])
def test_a_phrase_never_says_the_subject_twice(tag, operations):
    from app.connect import phrasing

    phrase = phrasing.phrase_for(tag, _group(tag, operations))
    verb, subject = phrase.action.split(" ", 1)

    assert verb != subject, phrase.action
    assert phrasing._singular(verb) != phrasing._singular(subject.split(" ")[0]), phrase.action
    # ...and not a near miss either: "tracks monitors", "manages management".
    assert not phrasing._says_the_same(phrase.verb, phrase.subject), phrase.action
    evidence = dict((n, w) for n, _b, _t, w in phrasing.VERB_FORMS)[phrase.verb]
    head = phrase.subject.split(" ")[0]
    assert head not in evidence and phrasing._singular(head) not in evidence, phrase.action


def test_the_thing_s_own_name_does_not_get_to_choose_the_verb():
    """A service saying "monitor" about something it calls a monitor has said
    what it is, not what is done with it. Left to vote, every group would
    describe itself in a circle."""
    from app.connect import phrasing

    phrase = phrasing.phrase_for("monitors", _group("monitors", COLLISIONS[0][1]))
    assert phrase.verb != "track", phrase.action
    assert "monitor" in phrase.subject


# --------------------------------------------------------------------------- machinery and what it is for

MACHINERY_GROUPS = [
    # tag, operations, the concept the service reveals behind its machinery
    (
        "runs",
        [
            ("get", "/api/v1/runs", "Runs, newest first"),
            ("post", "/api/v1/runs", "Create a run; no search is executed yet"),
            ("post", "/api/v1/runs/{id}/execute", "Execute the pending stages"),
            ("get", "/api/v1/runs/{id}/report", "The committed report"),
        ],
        "report",
    ),
    (
        "jobs",
        [
            ("get", "/api/v1/jobs", "Every job"),
            ("post", "/api/v1/jobs/{id}/execute", "Execute the job"),
            ("get", "/api/v1/jobs/{id}/results", "The results of the job"),
        ],
        "result",
    ),
    (
        "workflows",
        [
            ("get", "/api/v1/workflows", "Every workflow"),
            ("post", "/api/v1/workflows/{id}/start", "Start the workflow"),
            ("get", "/api/v1/workflows/{id}/analysis", "The analysis it produced"),
        ],
        "analysis",
    ),
]


@pytest.mark.parametrize(("tag", "operations", "concept"), MACHINERY_GROUPS, ids=[tag for tag, _ops, _c in MACHINERY_GROUPS])
def test_a_word_for_the_machinery_gives_way_to_what_the_machinery_is_for(tag, operations, concept):
    """A service calls something a "run" because that is what its code does
    with it. The person asked for whatever the run was of, and the service
    usually says so somewhere."""
    from app.connect import phrasing

    phrase = phrasing.phrase_for(tag, _group(tag, operations))
    assert concept in phrase.subject, phrase.action
    assert phrase.subject not in phrasing.MACHINERY, phrase.action


def test_machinery_is_still_used_when_the_service_offers_nothing_better():
    """Saying the plain thing beats saying nothing: a service whose runs are
    only ever runs is described as having runs."""
    from app.connect import phrasing

    phrase = phrasing.phrase_for("runs", _group("runs", [("get", "/api/v1/runs", "Every run"), ("post", "/api/v1/runs", "Create a run")]))
    assert "run" in phrase.subject
    assert not phrasing._says_the_same(phrase.verb, phrase.subject), phrase.action
