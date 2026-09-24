"""Services whose work is several typed operations, not one prompt.

Everything here goes through the generic machinery: a descriptor is read, an
operation catalogue is compiled, capabilities are inferred from the service's
own grouping, a scope is confirmed against the service's own listing, and a
plan is validated before anything is called. No test names a real product,
and nothing in `app/` or `adapters/` knows one exists.
"""

from __future__ import annotations

import pytest

from adapters.base import InvocationRequest, ProviderSpec
from adapters.openapi import (
    EXTERNAL_ACTION,
    READ_ONLY,
    STATE_CHANGE,
    WORK_EXECUTION,
    OpenApiAdapter,
    OperationPlan,
    PlanError,
    PlannedStep,
    plan_work_chain,
    validate_plan,
)
from app.connect import openapi
from app.connect.service import ConnectionDiscoveryService
from app.connect.strategies.base import DiscoveryContext, DiscoveryFailed
from app.connect.targets import classify_target
from app.operations import planner
from app.services import providers as provider_service
from tests import operational_fixtures as fx
import uuid


def discover(url: str, transport):
    return ConnectionDiscoveryService(use_assist=False).discover(classify_target(url), DiscoveryContext(roots=[], transport=transport))


# --------------------------------------------------------------------------- telling UI from API

def test_a_single_page_app_serves_html_everywhere_and_is_not_mistaken_for_a_description():
    """Entering a UI route must not make Bevro believe the page is the API.

    The shell answers 200 to `/p/someone/openapi.json` just as it answers
    everything else. What makes a description is the document, not the code.
    """
    transport, calls = fx.service(descriptor=fx.JOBS_API, spa=True)
    draft = discover("http://service.local/p/someone/", transport)

    assert draft.name == "Batch Service"
    assert draft.source["descriptor_url"] == "http://service.local/openapi.json"
    assert draft.source["origin"] == "http://service.local"
    assert draft.source["ui_base_path"] == "/p/someone/"
    # It did look under the entered path first, and refused what came back.
    assert "GET /p/someone/openapi.json" in calls


def test_a_description_under_the_entered_path_is_preferred():
    """A service that really does publish one there means it."""
    transport, _ = fx.service(descriptor=fx.JOBS_API, descriptor_path="/team/openapi.json", spa=True)
    draft = discover("http://service.local/team/", transport)
    assert draft.source["descriptor_url"] == "http://service.local/team/openapi.json"


def test_html_that_says_two_hundred_is_not_a_descriptor():
    transport, _ = fx.html_only()
    draft = discover("http://service.local/", transport)
    assert draft.mechanism != "openapi"
    assert not draft.invocable  # reachable is not the same as usable


def test_a_service_with_only_a_health_endpoint_is_not_connectable():
    """It answers, so discovery does not fail - but there is nothing to use."""
    transport, _ = fx.health_only()
    draft = discover("http://service.local/", transport)
    assert not draft.invocable
    assert draft.adapter.get("kind") != "openapi"


def test_the_shape_of_the_document_is_what_counts():
    assert openapi.looks_like_a_descriptor({"openapi": "3.1.0", "paths": {}}) is True
    assert openapi.looks_like_a_descriptor({"openapi": "3.1.0"}) is False  # no paths
    assert openapi.looks_like_a_descriptor({"paths": {}}) is False  # no version
    assert openapi.looks_like_a_descriptor("<html>") is False
    assert openapi.looks_like_a_descriptor({"items": []}) is False


def test_where_bevro_looks_for_a_description():
    where = openapi.descriptor_candidates("http://service.local/p/someone/")
    assert where[0] == "http://service.local/p/someone/openapi.json"
    assert "http://service.local/openapi.json" in where


# --------------------------------------------------------------------------- prompt vs operational

def test_a_service_with_one_prompt_endpoint_is_read_as_a_prompt_provider():
    transport, _ = fx.service(descriptor=fx.PROMPT_API)
    draft = discover("http://service.local/", transport)
    assert draft.adapter["kind"] == "http"  # one endpoint that takes a question
    assert draft.invocable


def test_a_service_of_typed_operations_is_read_as_an_operational_provider():
    transport, _ = fx.service(descriptor=fx.JOBS_API)
    draft = discover("http://service.local/", transport)

    assert draft.adapter["kind"] == "openapi"
    assert draft.runtime.kind == "openapi"
    config = draft.adapter["config"]
    assert len(config["operations"]) == 5
    assert draft.invocable


# --------------------------------------------------------------------------- the catalogue

def test_the_catalogue_keeps_what_matters_and_drops_the_rest():
    catalogue = openapi.compile_catalogue(fx.JOBS_API)
    create = next(op for op in catalogue if op.operation_id == "create_job")
    assert create.method == "POST" and create.path == "/api/jobs"
    assert create.body["label"]["type"] == "string"
    assert create.returns_fields == ["id"]

    execute = next(op for op in catalogue if op.operation_id == "execute_job")
    assert execute.parameters["job_id"] == {"in": "path", "required": True, "type": "string"}
    # Compact enough to send several of to a planner.
    assert len(fx.dumps(execute.compact())) < 500


def test_a_catalogue_survives_being_stored_and_read_back():
    catalogue = openapi.compile_catalogue(fx.JOBS_API)
    again = openapi.catalogue_from_json(openapi.catalogue_to_json(catalogue))
    assert [op.operation_id for op in again] == [op.operation_id for op in catalogue]
    assert [op.safety for op in again] == [op.safety for op in catalogue]


# --------------------------------------------------------------------------- what it can do

def test_capabilities_are_abilities_not_a_list_of_endpoints():
    """Forty operations do not become forty capabilities."""
    catalogue = openapi.compile_catalogue(fx.JOBS_API)
    capabilities = openapi.capabilities_from_operations(catalogue)
    assert [c["id"] for c in capabilities] == ["jobs", "stats"]
    assert capabilities[0]["description"] == "Jobs, newest first"  # the service's own words
    assert len(capabilities) < len(catalogue)


def test_the_router_sees_abilities_not_operations():
    transport, _ = fx.service(descriptor=fx.JOBS_API)
    draft = discover("http://service.local/", transport)
    shown = fx.dumps([c.model_dump() for c in draft.capabilities])
    assert "execute_job" not in shown and "POST" not in shown and "/api/jobs" not in shown


# --------------------------------------------------------------------------- scope

def test_a_route_that_names_a_profile_is_confirmed_against_the_service():
    """A guess from a URL only counts once the API agrees."""
    profiles = [{"profile_id": "alex", "display_name": "Alex Morgan"}, {"profile_id": "sam", "display_name": "Sam Lee"}]
    transport, _ = fx.service(descriptor=fx.scoped_api(profiles), spa=True, profiles=profiles)
    draft = discover("http://service.local/p/sam/", transport)

    assert draft.adapter["config"]["context"] == {"profile_id": "sam"}
    assert draft.source["connection_context"] == {"profile_id": "sam"}
    assert any("Sam Lee" in e for e in draft.evidence)
    assert draft.invocable and not draft.scope_choices


def test_a_route_naming_something_the_service_does_not_know_is_not_believed():
    profiles = [{"profile_id": "alex", "display_name": "Alex Morgan"}, {"profile_id": "sam", "display_name": "Sam Lee"}]
    transport, _ = fx.service(descriptor=fx.scoped_api(profiles), spa=True, profiles=profiles)
    draft = discover("http://service.local/p/nobody/", transport)

    assert draft.adapter["config"]["context"] == {}
    assert [c["value"] for c in draft.scope_choices] == ["alex", "sam"]
    assert not draft.invocable  # it has to be asked first


def test_several_to_choose_from_and_no_clue_asks_the_person():
    profiles = [{"profile_id": "alex", "display_name": "Alex Morgan"}, {"profile_id": "sam", "display_name": "Sam Lee"}]
    transport, _ = fx.service(descriptor=fx.scoped_api(profiles), spa=True, profiles=profiles)
    draft = discover("http://service.local/", transport)

    assert draft.scope_choices == [{"value": "alex", "label": "Alex Morgan"}, {"value": "sam", "label": "Sam Lee"}]
    assert any("choose" in w.lower() for w in draft.warnings)


def test_one_profile_and_no_clue_needs_no_question():
    profiles = [{"profile_id": "only", "display_name": "The Only One"}]
    transport, _ = fx.service(descriptor=fx.scoped_api(profiles), spa=True, profiles=profiles)
    draft = discover("http://service.local/", transport)
    assert draft.adapter["config"]["context"] == {"profile_id": "only"}
    assert not draft.scope_choices


def test_candidates_come_from_the_route_without_knowing_what_it_means():
    assert openapi.scope_candidates("/p/johannes/") == ["p", "johannes"]
    assert openapi.scope_candidates("/workspaces/acme/board") == ["workspaces", "acme", "board"]
    assert openapi.scope_candidates("/") == []


# --------------------------------------------------------------------------- safety

@pytest.mark.parametrize(
    ("method", "summary", "expected"),
    [
        ("GET", "Every profile", READ_ONLY),
        ("POST", "Run the validator (read-only; modifies nothing)", READ_ONLY),
        ("POST", "Execute the pending stages", WORK_EXECUTION),
        ("POST", "Record an external action or outcome (submitted, responses)", STATE_CHANGE),
        ("POST", "Send a test message (external action; needs confirm=true)", EXTERNAL_ACTION),
        ("PATCH", "Edit a strategy", STATE_CHANGE),
        ("POST", "Add one evidence row", STATE_CHANGE),
        ("POST", "Something entirely unexplained", "unknown"),
    ],
)
def test_the_service_own_words_decide_what_an_operation_is(method, summary, expected):
    """A POST the description calls read-only is read-only. A record of an
    external action is a record, not Bevro going and doing it."""
    assert openapi.classify(method, summary, "", []) == expected


def test_a_read_only_service_can_answer_but_not_work():
    transport, _ = fx.service(descriptor=fx.READ_ONLY_API)
    draft = discover("http://service.local/", transport)
    config = draft.adapter["config"]
    assert draft.invocable
    assert {op["safety"] for op in config["operations"]} == {READ_ONLY}
    # Asking it to destroy something cannot produce a destructive plan,
    # because it has nothing destructive to offer.
    chosen, _ = planner.plan("Delete every document.", config)
    safety = {next(o["safety"] for o in config["operations"] if o["operation_id"] == s.operation_id) for s in chosen.steps}
    assert safety == {READ_ONLY}


def test_changing_something_needs_the_request_to_ask_for_it():
    config = fx.as_config(fx.STATE_CHANGE_API, context={"case_id": "c1"})
    assert planner.allowed_safety("Show me the cases.") == {READ_ONLY}
    assert STATE_CHANGE in planner.allowed_safety("Record a decision on case c1.")

    change = OperationPlan(steps=[PlannedStep(operation_id="record_decision", arguments={"decision": "yes"})])
    with pytest.raises(PlanError, match="did not ask for"):
        validate_plan(change, config, allow={READ_ONLY})
    assert validate_plan(change, config, allow={READ_ONLY, STATE_CHANGE}).steps


def test_contacting_someone_outside_is_never_planned_on_its_own():
    config = fx.as_config(fx.EXTERNAL_API, context={"contact_id": "c1"})
    # It is not even offered to a planner.
    assert all(op["operation_id"] != "notify_contact" for op in planner.relevant_operations("Send a message to the contact", config))
    # And a plan naming it is refused whatever the request said.
    reach_out = OperationPlan(steps=[PlannedStep(operation_id="notify_contact")])
    with pytest.raises(PlanError, match="contact someone outside"):
        validate_plan(reach_out, config, allow=planner.allowed_safety("Send a message to the contact"))
    assert planner.needs_approval(reach_out, config) == ["Send a message to the contact (external action)"]


# --------------------------------------------------------------------------- planning

def test_a_plan_may_only_name_operations_the_service_described():
    config = fx.as_config(fx.JOBS_API)
    invented = OperationPlan(steps=[PlannedStep(operation_id="delete_everything")])
    with pytest.raises(PlanError, match="does not describe"):
        validate_plan(invented, config)


def test_a_plan_must_supply_what_an_operation_needs():
    config = fx.as_config(fx.JOBS_API)
    with pytest.raises(PlanError, match="job_id"):
        validate_plan(OperationPlan(steps=[PlannedStep(operation_id="execute_job")]), config)
    # ...unless an earlier step promises it. Creating the job is a change, so
    # it is allowed only as part of running one.
    chain = OperationPlan(steps=[PlannedStep(operation_id="create_job", extract={"job_id": "id"}), PlannedStep(operation_id="execute_job")])
    assert len(validate_plan(chain, config, allow={WORK_EXECUTION, READ_ONLY, STATE_CHANGE}).steps) == 2
    with pytest.raises(PlanError, match="change something"):
        validate_plan(chain, config, allow={WORK_EXECUTION, READ_ONLY})


def test_a_plan_cannot_run_for_ever():
    config = fx.as_config(fx.JOBS_API)
    too_many = OperationPlan(steps=[PlannedStep(operation_id="stats") for _ in range(9)])
    with pytest.raises(PlanError, match="at most"):
        validate_plan(too_many, config)


def test_a_value_the_schema_refuses_is_refused():
    config = fx.as_config(fx.STATE_CHANGE_API, context={"case_id": "c1"})
    bad = OperationPlan(steps=[PlannedStep(operation_id="record_decision", arguments={"decision": "maybe"})])
    with pytest.raises(PlanError, match="does not accept"):
        validate_plan(bad, config, allow={STATE_CHANGE, READ_ONLY})


def test_the_scope_a_connection_has_is_filled_in_rather_than_invented():
    config = fx.as_config(fx.scoped_api([]), context={"profile_id": "sam"})
    checked = validate_plan(OperationPlan(steps=[PlannedStep(operation_id="list_cases")]), config)
    assert checked.steps[0].arguments == {"profile_id": "sam"}


def test_one_read_is_planned_without_any_model():
    config = fx.as_config(fx.JOBS_API)
    chosen, allowed = planner.plan("Show me the statistics.", config)
    assert [s.operation_id for s in chosen.steps] == ["stats"]
    assert allowed == {READ_ONLY}


def test_work_is_planned_from_the_shape_of_the_paths_alone():
    """create → execute → report, worked out from the descriptor, no model."""
    config = fx.as_config(fx.JOBS_API)
    chosen, allowed = planner.plan("Run a new batch job.", config)
    assert [s.operation_id for s in chosen.steps] == ["create_job", "execute_job", "job_report"]
    assert chosen.steps[0].extract == {"job_id": "id"}
    assert WORK_EXECUTION in allowed


def test_a_planner_is_shown_a_handful_of_operations_not_the_whole_service():
    """A real descriptor is hundreds of kilobytes. None of it is sent."""
    big = {"openapi": "3.1.0", "info": {"title": "Big"}, "paths": {}}
    for n in range(200):
        big["paths"][f"/api/thing{n}"] = {"get": {"summary": f"Read thing {n} widgets", "operationId": f"thing_{n}", "tags": ["things"]}}
    config = fx.as_config(big)
    assert len(config["operations"]) == 200

    shown = planner.relevant_operations("Show me the widgets", config)
    assert len(shown) <= planner.CANDIDATES
    assert len(fx.dumps(shown)) < 6000  # a small fraction of the document


def test_a_model_proposal_that_does_not_fit_is_dropped_for_a_safe_read():
    config = fx.as_config(fx.JOBS_API)

    class Inventive:
        name = "test"

        def structured(self, system, user, schema, name, max_tokens=400):
            return {"steps": [{"operation_id": "drop_all_jobs", "arguments": {}}], "reason": "nonsense"}

    chosen, _ = planner.plan("Show me the statistics.", config, model=Inventive())
    assert [s.operation_id for s in chosen.steps] == ["stats"]  # the proposal was refused


def test_a_model_proposal_that_fits_is_used():
    config = fx.as_config(fx.JOBS_API)

    class Sensible:
        name = "test"

        def structured(self, system, user, schema, name, max_tokens=400):
            assert "drop_all" not in user
            return {"steps": [{"operation_id": "list_jobs", "label": "Looking at the jobs"}], "reason": "one read answers it"}

    chosen, _ = planner.plan("What jobs are there?", config, model=Sensible())
    assert [s.operation_id for s in chosen.steps] == ["list_jobs"]
    assert chosen.reason == "one read answers it"


# --------------------------------------------------------------------------- running it

def adapter_for(descriptor, transport, *, context=None, base="http://service.local"):
    config = fx.as_config(descriptor, base_url=base, context=context)
    spec = ProviderSpec(id="p", slug="s", name="Service", description="", capabilities=[], adapter={"kind": "openapi", "config": config})
    return OpenApiAdapter(transport=transport), spec, config


def test_several_operations_are_one_piece_of_work():
    transport, calls = fx.service(descriptor=fx.JOBS_API)
    adapter, spec, config = adapter_for(fx.JOBS_API, transport)
    chosen, allowed = planner.plan("Run a new batch job.", config)

    result = adapter.invoke(spec, InvocationRequest(task_id="t", run_id="r", request="Run a new batch job.", input={"operation_plan": chosen.model_dump(), "allowed_safety": sorted(allowed)}, secrets={}))

    assert result.state == "completed"
    # Three calls, one run, and the id from the first was carried into the rest.
    assert calls[-3:] == ["POST /api/jobs", "POST /api/jobs/job-1/execute", "GET /api/jobs/job-1/report"]
    assert [a.type for a in result.artifacts] == ["structured", "structured", "report"]
    assert result.metadata["operations"][2]["path"] == "/api/jobs/job-1/report"


def test_results_become_ordinary_artifacts_not_walls_of_json():
    transport, _ = fx.service(descriptor=fx.READ_ONLY_API, data={"/api/documents": [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"}]})
    adapter, spec, config = adapter_for(fx.READ_ONLY_API, transport)
    chosen, allowed = planner.plan("List the documents.", config)

    result = adapter.invoke(spec, InvocationRequest(task_id="t", run_id="r", request="List the documents.", input={"operation_plan": chosen.model_dump(), "allowed_safety": sorted(allowed)}, secrets={}))

    table = next(a for a in result.artifacts if a.type == "structured")
    assert table.payload["columns"] == ["id", "title"]
    assert table.payload["rows"] == [["a", "One"], ["b", "Two"]]
    assert "2 found" in result.summary


def test_markdown_comes_back_as_a_report():
    transport, _ = fx.service(descriptor=fx.JOBS_API)
    adapter, spec, config = adapter_for(fx.JOBS_API, transport, context={"job_id": "job-9"})
    chosen = validate_plan(OperationPlan(steps=[PlannedStep(operation_id="job_report")]), config)

    result = adapter.invoke(spec, InvocationRequest(task_id="t", run_id="r", request="Show the report.", input={"operation_plan": chosen.model_dump()}, secrets={}))
    report = next(a for a in result.artifacts if a.type == "report")
    assert report.mime_type == "text/markdown" and "Two things happened" in report.payload["text"]


def test_a_service_that_says_no_is_reported_plainly():
    def handler(request):
        if request.url.path == "/openapi.json":
            import httpx as _h

            return _h.Response(200, json=fx.JOBS_API, headers={"Content-Type": "application/json"})
        import httpx as _h

        return _h.Response(503, json={"detail": "The batch runner is down for maintenance."})

    import httpx

    transport = httpx.MockTransport(handler)
    adapter, spec, config = adapter_for(fx.JOBS_API, transport)
    chosen = validate_plan(OperationPlan(steps=[PlannedStep(operation_id="stats")]), config)

    result = adapter.invoke(spec, InvocationRequest(task_id="t", run_id="r", request="Statistics.", input={"operation_plan": chosen.model_dump()}, secrets={}))
    assert result.state == "failed"
    assert "503" in result.summary and "Counts" in result.summary
    assert result.error == "The batch runner is down for maintenance."


# --------------------------------------------------------------------------- testing a connection

def test_testing_a_connection_reads_and_never_starts_work():
    transport, calls = fx.service(descriptor=fx.JOBS_API)
    adapter, spec, _ = adapter_for(fx.JOBS_API, transport)

    assert adapter.health(spec, {}).ok is True
    assert all(call.startswith("GET ") for call in calls), calls  # nothing was created or run


def test_a_connection_whose_service_stopped_describing_itself_is_not_healthy():
    import httpx

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=fx.SPA_HTML, headers={"Content-Type": "text/html"}))
    adapter, spec, _ = adapter_for(fx.JOBS_API, transport)
    health = adapter.health(spec, {})
    assert health.ok is False and "describes itself" in (health.detail or "")


# --------------------------------------------------------------------------- remembering where it came from

def test_everything_needed_to_find_it_again_is_kept():
    transport, _ = fx.service(descriptor=fx.JOBS_API, spa=True)
    draft = discover("http://service.local/p/someone/", transport)
    source = draft.source
    assert source["kind"] == "http"
    assert source["entered_url"] == "http://service.local/p/someone/"
    assert source["origin"] == "http://service.local"
    assert source["ui_base_path"] == "/p/someone/"
    assert source["descriptor_url"] == "http://service.local/openapi.json"
    assert source["discovery_method"] == "openapi"
    assert "connection_context" in source


def test_choosing_which_one_makes_the_connection_usable(client, seeded, monkeypatch):
    """The question is asked, answered, and the answer is what gets stored."""
    from app.connect.strategies.command import CommandStrategy
    from app.connect.strategies.http import HttpDiscoveryStrategy
    from app.connect.strategies.local import LocalProjectStrategy
    from app.connect.service import set_discovery_service

    profiles = [{"profile_id": "alex", "display_name": "Alex Morgan"}, {"profile_id": "sam", "display_name": "Sam Lee"}]
    transport, _ = fx.service(descriptor=fx.scoped_api(profiles), spa=True, profiles=profiles)
    set_discovery_service(ConnectionDiscoveryService([HttpDiscoveryStrategy(transport), LocalProjectStrategy(), CommandStrategy()], use_assist=False))

    body = client.post("/api/connect/discover", json={"target": "http://service.local/"}).json()
    assert [c["value"] for c in body["draft"]["scope_choices"]] == ["alex", "sam"]

    refused = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={})
    assert refused.status_code == 422 and "which one" in refused.json()["detail"]

    provider = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={"scope": "sam"}).json()
    row = provider_service.get_provider(seeded, uuid.UUID(provider["id"]))
    assert (row.adapter.get("config") or {}).get("context") == {"profile_id": "sam"}
    assert row.source["connection_context"] == {"profile_id": "sam"}
