"""Routing: catalogue sanitisation, decision validation, the LLM router with a
fake model, fallback, and the boundary that nothing secret leaves Bevro."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from adapters import HealthResult
from app.config import get_settings
from app.models import Workspace
from app.routing import RoutingDecision, RoutingSource, get_router, set_router
from app.routing.catalogue import CatalogueCapability, CatalogueEntry, build_catalogue, catalogue_json
from app.routing.decision import InputRequestSpec, PlanStep, response_schema
from app.routing.deterministic import DeterministicRouter
from app.routing.llm import LLMRouter
from app.routing.models import RoutingModelError, get_routing_model
from app.routing.models._shared import parse_decision, user_message
from app.routing.models.anthropic import AnthropicRoutingModel
from app.routing.models.openai import OpenAIRoutingModel
from app.routing.router import build_router, routing_status
from app.routing.validate import InvalidDecision, validate_decision
from app.services import providers as provider_service
from app.services import tasks as task_service


class FakeModel:
    """A routing model that answers with whatever the test wants."""

    name = "fake"

    def __init__(self, decision: RoutingDecision | Exception | None = None, delay: float = 0.0) -> None:
        self.decision = decision
        self.delay = delay
        self.calls: list[dict] = []

    def route(self, request, catalogue, context):
        self.calls.append({"request": request, "catalogue": catalogue_json(catalogue), "context": context})
        if self.delay:
            time.sleep(self.delay)
        if isinstance(self.decision, Exception):
            raise self.decision
        assert self.decision is not None
        return self.decision.model_copy(update={"routing_source": RoutingSource.LLM})


def llm(decision, **kw) -> tuple[LLMRouter, FakeModel]:
    model = FakeModel(decision, **kw)
    return LLMRouter(model), model


def claude_available(db) -> None:
    provider_service.record_availability(db, provider_service.get_by_slug(db, "claude-code"), HealthResult(ok=True, state="available"))


@pytest.fixture(autouse=True)
def reset_router():
    yield
    set_router(None)


@pytest.fixture
def two_workspaces(seeded, tmp_path: Path):
    claude_available(seeded)
    for name in ("Alpha", "Beta"):
        d = tmp_path / name.lower()
        d.mkdir()
        seeded.add(Workspace(slug=name.lower(), name=name, path=str(d), permissions=["read", "write"]))
    seeded.flush()


# ----------------------------------------------------------------------------- catalogue

def test_catalogue_is_sanitised_and_filters_unavailable(seeded, tmp_path):
    claude = provider_service.get_by_slug(seeded, "claude-code")
    claude.adapter = {**claude.adapter, "config": {"cli": "/secret/place/claude"}}
    provider_service.register_provider(
        seeded,
        {"name": "Sales", "adapter": {"kind": "http", "config": {"base_url": "http://internal-sales:9000", "auth": {"type": "bearer", "secret": "api_key"}}}, "app_url": "http://sales.example"},
    )
    seeded.add(Workspace(slug="w", name="W", path=str(tmp_path), permissions=["read"]))
    seeded.flush()

    text = json.dumps(catalogue_json(build_catalogue(seeded, selectable_only=False)))
    for forbidden in ("/secret/place", "internal-sales", "base_url", "api_key", str(tmp_path), "config", "adapter", "app_url", "checked_at"):
        assert forbidden not in text, forbidden

    selectable = {e.id for e in build_catalogue(seeded)}
    assert "claude-code" not in selectable  # no worker has reported
    assert {"research", "calendar-demo", "sales"} <= selectable
    claude_available(seeded)
    entry = next(e for e in build_catalogue(seeded) if e.id == "claude-code")
    assert entry.requires == ["workspace"] and entry.can_invoke and entry.available
    assert {c.id for c in entry.capabilities} >= {"coding", "debugging"}


# ----------------------------------------------------------------------------- decision + validation

def test_response_schema_has_no_bevro_only_fields():
    schema = response_schema()
    assert "routing_source" not in schema["properties"]
    assert set(schema["properties"]) >= {"selected_provider_ids", "needs_input", "input_request", "plan", "confidence", "reason"}


def test_parse_decision_is_strict():
    ok = parse_decision('{"selected_provider_ids": ["research"], "needs_input": false, "confidence": 0.9}')
    assert ok.provider_id == "research" and ok.routing_source == RoutingSource.LLM
    for bad in ["not json", '{"selected_provider_ids": "research"}', '{"selected_provider_ids": [], "confidence": 7}', '{"selected_provider_ids": [], "extra": 1}', '[1,2]']:
        with pytest.raises(RoutingModelError):
            parse_decision(bad)


def test_validation_rejects_bad_decisions(seeded, two_workspaces):
    ok = validate_decision(seeded, RoutingDecision(selected_provider_ids=["research"]))
    assert ok.provider.slug == "research"
    assert validate_decision(seeded, RoutingDecision()).provider is None  # "nothing suitable" is a valid outcome

    with pytest.raises(InvalidDecision, match="unknown provider"):
        validate_decision(seeded, RoutingDecision(selected_provider_ids=["hallucinated-agent"]))
    with pytest.raises(InvalidDecision, match="multi-provider"):
        validate_decision(seeded, RoutingDecision(selected_provider_ids=["research", "claude-code"]))
    with pytest.raises(InvalidDecision, match="needs_input without an input_request"):
        validate_decision(seeded, RoutingDecision(selected_provider_ids=["research"], needs_input=True))
    stray = validate_decision(seeded, RoutingDecision(needs_input=True, input_request=InputRequestSpec(kind="question", prompt="Where?")))
    assert stray.provider is None and stray.decision.needs_input is False  # no provider: the question is moot
    with pytest.raises(InvalidDecision, match="does not take a workspace"):
        validate_decision(seeded, RoutingDecision(selected_provider_ids=["research"], needs_input=True, input_request=InputRequestSpec(kind="workspace")))
    with pytest.raises(InvalidDecision, match="unselected provider"):
        validate_decision(seeded, RoutingDecision(selected_provider_ids=["research"], plan=[PlanStep(goal="x", provider_id="claude-code")]))

    research = provider_service.get_by_slug(seeded, "research")
    research.enabled = False
    with pytest.raises(InvalidDecision, match="disabled"):
        validate_decision(seeded, RoutingDecision(selected_provider_ids=["research"]))
    research.enabled = True
    provider_service.record_availability(seeded, provider_service.get_by_slug(seeded, "claude-code"), HealthResult(ok=False, state="not_installed"))
    with pytest.raises(InvalidDecision, match="not available"):
        validate_decision(seeded, RoutingDecision(selected_provider_ids=["claude-code"]))


# ----------------------------------------------------------------------------- deterministic router

_RESEARCHER = ("research", "Research", [("research", "Research")])
_CODER = ("coder", "Coder", [("coding", "Write code"), ("debugging", "Fix bugs")])
_EVENTS = ("events", "Event Desk", [("events.allocate", "Allocate participants")])


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("Research the differences between PostgreSQL and MariaDB for this project.", "research"),
        ("Fix the spacing on the Recent page and run the frontend tests.", "coder"),
        ("Allocate the participants for the retreat.", "events"),
        ("Fix the failing test in the login code.", "coder"),
        # Nothing here is about any of them: no guess.
        ("Look into the login problem.", None),
        ("Write a poem about autumn.", None),
    ],
)
def test_deterministic_router_chooses_by_what_each_one_is_for(request_text, expected):
    entries = [_entry(*spec) for spec in (_RESEARCHER, _CODER, _EVENTS)]
    assert DeterministicRouter().decide(request_text, entries).provider_id == expected


def test_deterministic_router_never_routes_everything_to_research(seeded, two_workspaces):
    router = DeterministicRouter()
    poem = router.route(seeded, "Write a poem about autumn.")
    assert poem.selected_provider_ids == [] and poem.routing_source == RoutingSource.DETERMINISTIC
    coding = router.route(seeded, "Fix the bug.")
    assert coding.provider_id == "claude-code" and coding.needs_input and coding.input_request.kind == "workspace"
    with pytest.raises(task_service.NoProviderAvailable) as exc:
        task_service.submit(seeded, "Write a poem about autumn.")
    assert str(exc.value) == "I don't have an app or agent that looks suited to this yet." and exc.value.reason == "no_provider"


def _entry(id: str, name: str, caps: list[tuple[str, str]]) -> CatalogueEntry:
    return CatalogueEntry(
        id=id,
        name=name,
        description="",
        capabilities=[CatalogueCapability(id=c, title=t) for c, t in caps],
        available=True,
        can_invoke=True,
    )


def test_one_word_routes_when_it_is_one_agent_s_own_word():
    """"Show me my shortlist" is a plain request naming nothing but the thing
    it wants. No keyword rule covers it, and one matching word is normally too
    little - but "shortlist" is what exactly one connected agent calls one of
    its capabilities, so there is no ambiguity to protect against."""
    desk = _entry("casework", "Casework control API", [("shortlist", "Shortlist"), ("applications", "Applications")])
    coder = _entry("claude-code", "Claude Code", [("coding", "Build, fix and change software")])
    decision = DeterministicRouter().decide("Show me my shortlist.", [desk, coder])
    assert decision.provider_id == "casework"


def test_a_word_two_agents_both_use_settles_nothing():
    a = _entry("a", "Alpha", [("reports", "Reports")])
    b = _entry("b", "Beta", [("reports", "Reports")])
    assert DeterministicRouter().decide("Show me my reports.", [a, b]).selected_provider_ids == []


def test_an_ordinary_word_in_a_capability_title_is_not_a_match():
    """Titles are written in English: "Build, fix and change software" must not
    make "Write a poem" a coding request. Only the id counts on its own."""
    coder = _entry("claude-code", "Claude Code", [("coding", "Write, build and change software")])
    assert DeterministicRouter().decide("Write a poem about autumn.", [coder]).selected_provider_ids == []


# ----------------------------------------------------------------------------- LLM router

def test_llm_router_selects_a_valid_provider_and_records_metadata(seeded, two_workspaces):
    router, model = llm(RoutingDecision(selected_provider_ids=["research"], rationale="capabilities fit", plan=[PlanStep(goal="Compare")], confidence=0.9))
    set_router(router)
    # Nothing in what the agents say about themselves settles this one: the model is asked.
    task = task_service.submit(seeded, "Help me decide between the two options we discussed.")
    assert task.runs[0].provider.slug == "research"
    assert task.routing["source"] == "llm" and task.routing["backend"] == "fake"
    assert task.routing["selected_provider_ids"] == ["research"] and task.routing["confidence"] == 0.9
    assert task.routing["plan"] == [{"goal": "Compare", "provider_id": None}] and task.routing["fallback_reason"] is None
    assert model.calls[0]["request"].startswith("Help me decide")


def test_an_obvious_request_never_calls_the_model(seeded, two_workspaces):
    router, model = llm(RoutingDecision(selected_provider_ids=["research"]))
    set_router(router)
    task = task_service.submit(seeded, "Move my meetings to free up Friday afternoon")
    assert task.runs[0].provider.slug == "calendar-demo" and task.routing["source"] == "deterministic"
    assert model.calls == []


def test_llm_router_needs_input_for_workspace(seeded, two_workspaces):
    router, model = llm(RoutingDecision(selected_provider_ids=["claude-code"], needs_input=True, input_request=InputRequestSpec(kind="workspace", prompt="Which project should I work on?")))
    set_router(router)
    task = task_service.submit(seeded, "Fix the bug.")
    assert task.state == "needs_input"
    assert [o["label"] for o in task.runs[0].input_request["options"]] == ["Alpha", "Beta"]
    # Which project to work in is Bevro's question, and a coding request is obvious: no model call.
    assert model.calls == []


def test_llm_router_can_ask_one_question(seeded, two_workspaces):
    set_router(llm(RoutingDecision(selected_provider_ids=["research"], needs_input=True, input_request=InputRequestSpec(kind="question", prompt="Which two databases?")))[0])
    task = task_service.submit(seeded, "Compare them.")
    assert task.state == "needs_input" and task.summary == "Which two databases?"
    run = task.runs[0]
    assert run.input_request == {"question": "Which two databases?", "kind": "text", "field": "answer", "options": []}
    with pytest.raises(task_service.InvalidInput):
        task_service.answer_input(seeded, task, "   ")
    task_service.answer_input(seeded, task, "PostgreSQL and MariaDB")
    assert task.state == "queued" and run.input["answer"] == "PostgreSQL and MariaDB"


def test_llm_router_no_provider_decision(seeded, two_workspaces):
    set_router(llm(RoutingDecision(reason="No available provider supports this request."))[0])
    with pytest.raises(task_service.NoProviderAvailable) as exc:
        task_service.submit(seeded, "Write a poem about autumn.")
    assert exc.value.reason == "no_provider"


@pytest.mark.parametrize(
    ("bad", "reason_fragment"),
    [
        (RoutingDecision(selected_provider_ids=["hallucinated-agent"]), "unknown provider"),
        (RoutingDecision(selected_provider_ids=["research", "claude-code"]), "multi-provider"),
        (RoutingModelError("routing model returned non-JSON output"), "non-JSON"),
        (RoutingModelError("routing model timed out"), "timed out"),
        (RuntimeError("boom"), "unexpected RuntimeError"),
    ],
)
def test_llm_failures_fall_back_to_deterministic(seeded, two_workspaces, bad, reason_fragment):
    router, _ = llm(bad)
    set_router(router)
    # Thin evidence for the research agent, so the model is asked - and fails.
    task = task_service.submit(seeded, "Compare them.")
    assert task.routing["source"] == "fallback"
    assert reason_fragment in task.routing["fallback_reason"]
    assert task.runs[0].provider.slug == "research"  # what the words found stands


def test_llm_rejects_disabled_and_unavailable_providers(seeded, two_workspaces):
    provider_service.record_availability(seeded, provider_service.get_by_slug(seeded, "claude-code"), HealthResult(ok=False, state="not_installed"))
    router, model = llm(RoutingDecision(selected_provider_ids=["claude-code"]))
    set_router(router)
    with pytest.raises(task_service.NoProviderAvailable, match="^Claude Code is the right one for this, but"):
        task_service.submit(seeded, "Fix the login bug.")
    # The right one, unavailable, is said as such: nothing else is tried in its place.
    assert model.calls == []
    calendar_demo = provider_service.get_by_slug(seeded, "calendar-demo")
    calendar_demo.enabled = False
    router, model = llm(RoutingDecision(selected_provider_ids=["calendar-demo"]))
    set_router(router)
    task = task_service.submit(seeded, "Compare the venues.")
    assert task.routing["source"] == "fallback"
    assert "disabled" in task.routing["fallback_reason"] or "unknown" in task.routing["fallback_reason"]
    assert all(e["id"] != "calendar-demo" for e in model.calls[0]["catalogue"])  # a paused one is not even offered
    assert task.runs[0].provider.slug == "research"


def test_explicit_provider_choice_bypasses_the_router(seeded, two_workspaces):
    router, model = llm(RoutingDecision(selected_provider_ids=["research"]))
    set_router(router)
    claude = provider_service.get_by_slug(seeded, "claude-code")
    task = task_service.submit(seeded, "Compare things", provider=claude)
    assert task.runs[0].provider.slug == "claude-code"
    assert task.routing["source"] == "explicit" and model.calls == []


# ----------------------------------------------------------------------------- backends + configuration

def test_deterministic_mode_never_calls_a_model(seeded, monkeypatch):
    monkeypatch.setenv("BEVRO_ROUTER_MODE", "deterministic")
    monkeypatch.setenv("BEVRO_ROUTER_API_KEY", "should-never-be-used")
    get_settings.cache_clear()

    def explode(*args, **kwargs):
        raise AssertionError("an HTTP call was made in deterministic mode")

    monkeypatch.setattr(httpx.Client, "post", explode)
    monkeypatch.setattr(httpx.Client, "send", explode)
    try:
        set_router(build_router())
        assert get_router().name == "deterministic" and routing_status() == {"mode": "deterministic"}
        task = task_service.submit(seeded, "Compare two things")
        assert task.routing["source"] == "deterministic"
    finally:
        get_settings.cache_clear()


def test_llm_mode_without_a_key_degrades_to_deterministic(monkeypatch):
    monkeypatch.setenv("BEVRO_ROUTER_MODE", "llm")
    monkeypatch.setenv("BEVRO_ROUTER_BACKEND", "openai")
    monkeypatch.setenv("BEVRO_ROUTER_API_KEY", "")
    get_settings.cache_clear()
    try:
        router = build_router()
        assert router.name == "llm"  # the backend exists; the missing key surfaces as a fallback per request, not a crash
        monkeypatch.setenv("BEVRO_ROUTER_BACKEND", "carrier-pigeon")
        get_settings.cache_clear()
        assert build_router().name == "deterministic"
    finally:
        get_settings.cache_clear()


def _capture_transport(captured: dict, body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def test_openai_backend_sends_only_sanitised_context(seeded, two_workspaces, tmp_path, monkeypatch):
    monkeypatch.setenv("BEVRO_ROUTER_API_KEY", "sk-test-key")
    monkeypatch.setenv("BEVRO_ROUTER_MODEL", "tiny-model")
    get_settings.cache_clear()
    try:
        captured: dict = {}
        answer = {"choices": [{"message": {"content": json.dumps({"selected_provider_ids": ["research"], "needs_input": False, "input_request": None, "rationale": "r", "plan": [], "confidence": 0.8, "reason": None})}}]}
        model = OpenAIRoutingModel(get_settings(), transport=_capture_transport(captured, answer))
        decision = model.route("Compare X and Y", build_catalogue(seeded), {"previous_question": "Which?", "answer": "X and Y"})
        assert decision.provider_id == "research" and decision.routing_source == RoutingSource.LLM
        payload = captured["json"]
        assert payload["model"] == "tiny-model" and payload["response_format"]["json_schema"]["strict"] is True
        assert captured["headers"]["authorization"] == "Bearer sk-test-key"
        sent = json.dumps(payload)
        for forbidden in ("sk-test-key", str(tmp_path), "/home", "config", "adapter", "base_url", "worker", "cli"):
            assert forbidden not in sent, forbidden
        user = json.loads(payload["messages"][1]["content"])
        assert set(user) == {"request", "providers", "previous_question", "answer"}
        assert len(json.dumps(user)) < 4000  # restrained context
    finally:
        get_settings.cache_clear()


def test_openai_backend_failures_become_routing_errors(seeded, two_workspaces, monkeypatch):
    monkeypatch.setenv("BEVRO_ROUTER_API_KEY", "sk-test-key")
    get_settings.cache_clear()
    try:
        catalogue = build_catalogue(seeded)
        for response in [httpx.Response(401, json={"error": "bad key"}), httpx.Response(200, json={"choices": []}), httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})]:
            model = OpenAIRoutingModel(get_settings(), transport=httpx.MockTransport(lambda r, resp=response: resp))
            with pytest.raises(RoutingModelError):
                model.route("x", catalogue, {})

        def slow(request):
            raise httpx.ReadTimeout("slow")

        model = OpenAIRoutingModel(get_settings(), transport=httpx.MockTransport(slow))
        with pytest.raises(RoutingModelError, match="timed out"):
            model.route("x", catalogue, {})
    finally:
        get_settings.cache_clear()


def test_anthropic_backend_uses_forced_tool_call(seeded, two_workspaces, monkeypatch):
    monkeypatch.setenv("BEVRO_ROUTER_API_KEY", "sk-ant-test")
    monkeypatch.setenv("BEVRO_ROUTER_BACKEND", "anthropic")
    get_settings.cache_clear()
    try:
        captured: dict = {}
        answer = {"content": [{"type": "tool_use", "name": "routing_decision", "input": {"selected_provider_ids": [], "needs_input": False, "reason": "nothing fits"}}]}
        model = AnthropicRoutingModel(get_settings(), transport=_capture_transport(captured, answer))
        assert get_routing_model(get_settings()).name == "anthropic"
        decision = model.route("Write a poem", build_catalogue(seeded), {})
        assert decision.selected_provider_ids == [] and decision.reason == "nothing fits"
        assert captured["headers"]["x-api-key"] == "sk-ant-test"
        assert captured["json"]["tool_choice"] == {"type": "tool", "name": "routing_decision"}
        assert "sk-ant-test" not in json.dumps(captured["json"])
    finally:
        get_settings.cache_clear()


def test_user_message_is_bounded():
    text = user_message("x" * 10_000, [], {})
    assert len(json.loads(text)["request"]) == 2000
