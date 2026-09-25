"""Setting up direct access for something already in Bevro upgrades it in place.

One item, before and after: known, used through its own web app, and - once
the person has told Bevro how it takes a task - something Bevro can send
work to as well. Never a second entry. Synthetic shapes only.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from adapters.base import HealthResult
from app.connect import surfaces as surface
from app.models import Provider, ProviderRun, Task
from app.services import providers as provider_service
from app.services import runtime as runtime_service

WEB = surface.web_surface("http://127.0.0.1:6400", bind="all", title="Notebook")
SETUP = {"method": "api", "details": {"base_url": "http://notebook.lan:8000", "request_template": 'POST /ask {"q": "{request}"}', "response_field": "answer"}, "secrets": {}}


@pytest.fixture
def notebook(db):
    """A web app Bevro knows and can't drive, with a piece of work in its history."""
    p = provider_service.register_provider(
        db,
        {
            "name": "Notebook",
            "description": "Keeps what you have noted and learned.",
            "capabilities": [{"id": "knowledge", "title": "Search what you know"}],
            "adapter": {},
            "surfaces": [WEB],
            "source": {"target_kind": "url", "target": "http://notebook.lan:8080"},
            "origin": "connected",
        },
    )
    task = Task(title="Earlier", original_request="Earlier work")
    db.add(task)
    db.flush()
    db.add(ProviderRun(task_id=task.id, provider_id=p.id, provider_name=p.name, provider_slug=p.slug))
    db.commit()
    return p


def answers(monkeypatch, ok: bool = True):
    monkeypatch.setattr(runtime_service, "check_runtime", lambda p, rt, s: HealthResult(ok=ok, state="available" if ok else "unavailable", detail=None if ok else "Couldn't reach it (ConnectError)."))


def _count(db) -> int:
    return db.scalar(select(func.count()).select_from(Provider))


def test_A_to_E_the_same_item_gains_a_way_in(client, db, notebook, monkeypatch):
    answers(monkeypatch)
    before = client.get(f"/api/providers/{notebook.id}").json()
    assert before["direct"]["state"] == "not_set_up" and "ask" not in before["actions"]
    rows = _count(db)

    r = client.post(f"/api/providers/{notebook.id}/direct-access", json=SETUP)
    assert r.status_code == 200, r.text
    after = r.json()

    # A: the same item.
    assert after["id"] == str(notebook.id) and after["slug"] == before["slug"] and after["name"] == "Notebook"
    # B: how it is used, what it is for, and what it can do are all still there.
    assert after["surfaces"] == before["surfaces"]
    assert after["description"] == before["description"] and after["capabilities"] == before["capabilities"]
    # C: and now Bevro can send it work, through the way in just added.
    assert after["direct"]["state"] == "ready" and after["actions"] == ["ask", "open"]
    db.refresh(notebook)
    active = runtime_service.active_runtime(notebook)
    assert active is not None and active.invocable and active.adapter["config"]["base_url"] == "http://notebook.lan:8000"
    # D: no second entry.
    assert _count(db) == rows
    assert [p["name"] for p in client.get("/api/providers").json()].count("Notebook") == 1
    # E: its history is its own still.
    assert db.scalar(select(func.count()).select_from(ProviderRun).where(ProviderRun.provider_id == notebook.id)) == 1


def test_F_nothing_changes_until_setup_is_confirmed(client, db, notebook):
    """Opening Advanced setup and leaving it sends nothing; the item is as it was."""
    before = client.get(f"/api/providers/{notebook.id}").json()
    assert client.get(f"/api/providers/{notebook.id}").json() == before
    assert before["direct"]["state"] == "not_set_up"


def test_G_a_way_in_that_does_not_answer_is_refused_and_changes_nothing(client, db, notebook, monkeypatch):
    answers(monkeypatch, ok=False)
    before = client.get(f"/api/providers/{notebook.id}").json()
    runtimes = list(notebook.runtimes)
    r = client.post(f"/api/providers/{notebook.id}/direct-access", json=SETUP)
    assert r.status_code == 409 and "Nothing about Notebook was changed" in r.json()["detail"]
    after = client.get(f"/api/providers/{notebook.id}").json()
    assert after["direct"]["state"] == "not_set_up" and "ask" not in after["actions"]
    assert {k: v for k, v in after.items() if k != "updated_at"} == {k: v for k, v in before.items() if k != "updated_at"}
    db.refresh(notebook)
    assert notebook.runtimes == runtimes


def test_G_details_that_make_no_sense_are_refused_before_anything_is_touched(client, notebook):
    r = client.post(f"/api/providers/{notebook.id}/direct-access", json={"method": "api", "details": {"base_url": "notebook.lan"}, "secrets": {}})
    assert r.status_code == 422
    assert client.get(f"/api/providers/{notebook.id}").json()["direct"]["state"] == "not_set_up"


def test_looking_again_keeps_what_the_person_set_up(client, db, notebook, monkeypatch):
    """Rediscovery replaces what discovery found; a way in set up by hand is
    not something it can find again, so it stays - and stays in use."""
    answers(monkeypatch)
    client.post(f"/api/providers/{notebook.id}/direct-access", json=SETUP)
    db.refresh(notebook)
    from app.services import connect as connect_service

    page = connect_service.ProviderDraft(name="Notebook", mechanism="http", invocable=False, surfaces=[WEB])

    class Found:
        def discover(self, target, context):
            return page

    monkeypatch.setattr(connect_service, "get_discovery_service", lambda: Found())
    connect_service._reconnect_here(db, notebook)
    db.refresh(notebook)
    assert runtime_service.active_runtime(notebook).adapter["config"].get("by_hand") is True
    assert client.get(f"/api/providers/{notebook.id}").json()["direct"]["state"] == "ready"


def test_an_unknown_item_is_not_created_by_setting_up_access(client, db):
    import uuid

    rows = _count(db)
    assert client.post(f"/api/providers/{uuid.uuid4()}/direct-access", json=SETUP).status_code == 404
    assert _count(db) == rows


def test_H_setting_up_something_new_still_creates_it(client, db):
    rows = _count(db)
    r = client.post("/api/providers", json={"name": "Runner", "description": "", "capabilities": ["Reports"], "method": "api", "details": {"base_url": "http://runner.lan"}, "secrets": {}, "app_url": None})
    assert r.status_code == 201
    assert _count(db) == rows + 1 and r.json()["name"] == "Runner"
