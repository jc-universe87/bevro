"""From a goal to the right app or agent, and to the way to use it now.

The person says what they want done; Bevro works out which of their things
is for that, how sure it is, and only then whether it can send the work
itself (docs/ROUTING.md). Every item here is synthetic: shapes of real
things, never their names.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.connect import surfaces as surface
from app.models import Task
from app.routing.resolve import resolve
from app.services import providers as provider_service
from tests import operational_fixtures as fx

WEB = surface.web_surface("http://127.0.0.1:6400", bind="all", title="Web app")
SCHEDULE = surface.Surface(kind="schedule", role="runs")
CHAT = surface.Surface(kind="telegram", role="delivers")


def _item(db, name: str, *, caps=(), description: str = "", source: str | None = None, direct: str | None = None, surfaces=()):
    """direct: None (no way in), "ready", "broken" (stopped answering) or "credential" (needs one)."""
    runtimes = []
    if direct is not None:
        rt = fx.network_runtime("openapi", api="available", base=f"http://{name.lower().replace(' ', '-')}.local")
        if direct == "broken":
            rt = rt.model_copy(update={"health": rt.health.after_failure("Couldn't reach it.")})
        if direct == "credential":
            rt = rt.model_copy(update={"credentials": rt.credentials.model_copy(update={"names": ["SERVICE_KEY"], "required_from_user": True})})
        runtimes.append(rt)
    provider = provider_service.register_provider(
        db,
        {
            "name": name,
            "description": description,
            "source_description": source,
            "capabilities": [dict(c) for c in caps],
            "adapter": {},
            "runtimes": runtimes,
            "surfaces": list(surfaces),
            "origin": "connected",
        },
    )
    db.commit()
    return provider


def cap(id: str, title: str, subject: str | None = None, **more):
    return {"id": id, "title": title, **({"subject": subject} if subject else {}), **more}


def jobs_desk(db, direct: str = "ready"):
    return _item(
        db,
        "Jobs Desk",
        caps=[cap("shortlist", "Review opportunities", "opportunities", terms=["opportunities"]), cap("applications", "Review applications", "applications"), cap("vacancies", "Review vacancies", "vacancies")],
        direct=direct,
    )


def researcher(db, name: str = "Researcher", description: str = "Researches anything and writes a report.", direct: str = "ready"):
    return _item(db, name, caps=[cap("research", "Research"), cap("reporting", "Reports"), cap("review", "Review")], description=description, direct=direct)


def notebook(db):
    return _item(db, "Notebook", caps=[cap("knowledge", "Search what you know")], description="Keeps what you have noted, learned and thought about.", surfaces=[WEB])


def binder(db):
    return _item(db, "Binder", caps=[cap("archive", "Filing and finding documents")], source="Describe a paper document to find out which binder it lives in.", surfaces=[WEB])


# --------------------------------------------------------------------------- which one

def test_A_specific_career_words_beat_a_general_research_agent(db):
    jobs_desk(db)
    researcher(db)
    answer = resolve(db, "Help me review my current job opportunities.")
    assert answer.provider.name == "Jobs Desk" and answer.sure and answer.outcome == "direct"
    assert answer.message == "Jobs Desk can handle this directly."


def test_B_where_did_i_write_means_notes_not_a_document_store(db):
    notebook(db)
    _item(db, "Doc Store", caps=[cap("documents", "Documents")], description="Stores documents and files.", surfaces=[WEB])
    answer = resolve(db, "Where did I write about pricing?")
    assert answer.provider.name == "Notebook" and answer.sure


def test_C_where_is_something_filed_means_the_archive(db):
    notebook(db)
    binder(db)
    answer = resolve(db, "Where is my passport scan filed?")
    assert answer.provider.name == "Binder" and answer.sure


def test_D_research_in_a_project_s_own_domain_goes_to_that_project_s_agent(db):
    researcher(db)
    _item(
        db,
        "Market Watch",
        caps=[cap("research", "Research"), cap("competitor_analysis", "Competitor analysis"), cap("market_analysis", "Market analysis")],
        description="Researches changes in the bicycle market that matter to Velocity.",
        direct="ready",
    )
    answer = resolve(db, "Research new competitors for Velocity.")
    assert answer.provider.name == "Market Watch" and answer.sure


def test_E_a_bare_research_this_with_several_research_agents_asks(db):
    researcher(db)
    researcher(db, "Scout", "Researches and summarises topics.")
    answer = resolve(db, "Research this")
    assert answer.outcome == "choice" and {o.provider.name for o in answer.choices} == {"Researcher", "Scout"}
    assert answer.provider is None


def test_F_what_the_person_said_it_is_for_is_strong_evidence(db):
    _item(db, "Hearth", caps=[cap("meal_planning", "Meal planning", by="person")], surfaces=[WEB])
    _item(db, "Recipe Box", source="Stores recipes and meals you have cooked before.", surfaces=[WEB])
    answer = resolve(db, "Plan my meals for next week")
    assert answer.provider.name == "Hearth" and answer.sure
    assert answer.why == "You said Hearth is for meal planning."


def test_G_how_something_is_built_says_nothing_about_what_it_is_for(db):
    _item(db, "Pantry", source="A FastAPI service with a Docker Compose setup and a Postgres database that tracks groceries.", surfaces=[WEB])
    assert resolve(db, "Which database should I use with Docker Compose?").outcome == "none"
    assert resolve(db, "Which groceries are running low?").provider.name == "Pantry"


def test_O_the_item_s_own_specific_words_outrank_generic_ones(db):
    jobs_desk(db)
    researcher(db)
    answer = resolve(db, "Review the vacancies report")
    assert answer.provider.name == "Jobs Desk"


# --------------------------------------------------------------------------- how sure

def test_K_nothing_that_fits_is_said_plainly(db):
    jobs_desk(db)
    notebook(db)
    answer = resolve(db, "Book me a dentist appointment.")
    assert answer.outcome == "none" and answer.provider is None
    assert answer.message == "I don't have an app or agent that looks suited to this yet."


def test_L_two_close_matches_are_a_choice_not_a_guess(db):
    _item(db, "Ledger", caps=[cap("documents", "Prepare documents", "documents")], direct="ready")
    binder(db)
    answer = resolve(db, "I need to work on some documents")
    assert answer.outcome == "choice" and [o.provider.name for o in answer.choices] == ["Ledger", "Binder"]
    assert answer.message == "I found two apps that could help."
    assert all(o.summary for o in answer.choices)


def test_a_choice_says_what_each_one_is_for_in_a_sentence(db):
    from app.routing.fit import summary

    assert summary(jobs_desk(db)) == "Review opportunities, review applications and review vacancies"
    assert summary(notebook(db)) == "Keeps what you have noted, learned and thought about."


def test_N_a_vague_request_is_not_matched_to_anything(db):
    jobs_desk(db)
    notebook(db)
    researcher(db)
    for vague in ("Do the thing", "Sort it out", "Can you help?"):
        assert resolve(db, vague).outcome == "none", vague


def test_asking_which_apps_could_help_lists_them_rather_than_starting_work(db):
    researcher(db)
    jobs_desk(db)
    answer = resolve(db, "What apps can help me research something?")
    assert answer.outcome == "choice" and [o.provider.name for o in answer.choices] == ["Researcher"]


def test_naming_an_item_settles_it(db):
    researcher(db)
    researcher(db, "Scout", "Researches and summarises topics.")
    answer = resolve(db, "Ask Scout to research bike locks")
    assert answer.provider.name == "Scout" and answer.sure


# --------------------------------------------------------------------------- how to use it now

def test_H_the_right_one_being_unreachable_is_said_and_nothing_worse_is_tried(db):
    jobs_desk(db, direct="broken")
    researcher(db)
    answer = resolve(db, "Help me review my current job opportunities.")
    assert answer.provider.name == "Jobs Desk" and answer.outcome == "unavailable"
    assert answer.message == "Jobs Desk is the right one for this, but I can't reach it right now."


def test_I_a_web_only_app_is_a_real_answer(db):
    notebook(db)
    answer = resolve(db, "Find my notes about the garden")
    assert answer.outcome == "handoff" and answer.message == "Notebook is the best place for this."


def test_J_a_missing_credential_is_said_before_anything_runs(db):
    _item(db, "Briefing", caps=[cap("research", "Research"), cap("market_analysis", "Market analysis")], description="Researches the widget market.", direct="credential", surfaces=[SCHEDULE, CHAT])
    answer = resolve(db, "What are the latest widget market trends?")
    assert answer.outcome == "blocked"
    assert answer.message == "Briefing can do this, but direct use in Bevro needs a credential."


def test_something_with_no_app_but_a_known_way_to_use_it_is_explained(db):
    _item(db, "Digest", caps=[cap("summarise", "Summarise")], description="Summarises the week's news.", surfaces=[SCHEDULE, CHAT])
    answer = resolve(db, "Summarise this week's news")
    assert answer.outcome == "how_to" and answer.provider.name == "Digest"


def test_something_known_with_no_way_to_use_it_says_it_needs_setting_up(db):
    _item(db, "Oracle", caps=[cap("forecasting", "Forecasting")], description="Forecasts sales.")
    answer = resolve(db, "Forecast next quarter's sales")
    assert answer.outcome == "setup" and answer.message == "Oracle fits this, but Bevro doesn't know how to reach it yet."


# --------------------------------------------------------------------------- only what the person has

def test_M_a_removed_item_is_never_considered_and_a_re_added_one_is(client, db):
    p = notebook(db)
    assert client.delete(f"/api/providers/{p.id}").status_code == 204
    assert resolve(db, "Where did I write about pricing?").outcome == "none"
    notebook(db)
    assert resolve(db, "Where did I write about pricing?").provider.name == "Notebook"


def test_a_paused_item_is_not_considered(db):
    p = notebook(db)
    p.enabled = False
    db.commit()
    assert resolve(db, "Where did I write about pricing?").outcome == "none"


# --------------------------------------------------------------------------- the answer Home gets

def test_routing_creates_nothing_and_says_only_plain_things(client, db):
    jobs_desk(db)
    notebook(db)
    before = db.query(Task).count()
    for text in ("Help me review my current job opportunities.", "Where did I write about pricing?", "Book a table"):
        r = client.post("/api/route", json={"request": text})
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"outcome", "message", "sure", "item", "why", "choices"}
        assert isinstance(body["sure"], bool)
        # No scores, no internals: nothing numeric, no machinery words.
        assert not re.search(r"\d\.\d|score|weight|evidence|capability|runtime|adapter|provider", r.text, re.I), r.text
    assert db.query(Task).count() == before


def test_the_person_s_own_choice_is_answered_for_that_item(client, db):
    ledger = _item(db, "Ledger", caps=[cap("documents", "Prepare documents", "documents")], direct="ready")
    b = binder(db)
    r = client.post("/api/route", json={"request": "I need to work on some documents", "provider_id": str(b.id)}).json()
    assert r["outcome"] == "handoff" and r["item"]["name"] == "Binder" and r["sure"] is True
    r = client.post("/api/route", json={"request": "I need to work on some documents", "provider_id": str(ledger.id)}).json()
    assert r["outcome"] == "direct" and r["item"]["name"] == "Ledger"


def test_a_handoff_through_tasks_creates_no_task(client, db):
    notebook(db)
    before = db.query(Task).count()
    r = client.post("/api/tasks", json={"request": "Where did I write about pricing?"})
    assert r.status_code == 503 and r.json()["detail"]["answer"]["outcome"] == "handoff"
    assert db.query(Task).count() == before


def test_P_routing_names_no_real_app():
    """Kinds of work, never particular apps: the routing code must not know
    the names of anything a person actually uses."""
    routing = Path(__file__).resolve().parents[1] / "app" / "routing"
    text = " ".join(p.read_text().lower() for p in routing.rglob("*.py"))
    for name in ("zekor", "archivist", "moimio", "career agent", "career-agent"):
        assert name not in text, name
