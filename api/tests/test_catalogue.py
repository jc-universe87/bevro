"""Apps & agents is the person's own list.

Connect or Create puts something in it; Remove from Bevro takes it out, for
good; connecting it again later works like the first time. Nothing appears
because Bevro has code for it - support is offered, not added. History is
never lost on the way. Synthetic shapes only.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from adapters.registry import adapter_kinds
from app.connect import surfaces as surface
from app.models import Provider, ProviderRun, ProviderSecret, Task
from app.services import providers as provider_service

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def page_at_notebook():
    """Discovery sees one page for people at http://notebook.lan:8080."""
    from app.connect.service import ConnectionDiscoveryService, set_discovery_service
    from app.connect.strategies.http import HttpDiscoveryStrategy

    def page(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text="<html><title>Notebook</title></html>", headers={"Content-Type": "text/html"})
        return httpx.Response(404)

    set_discovery_service(ConnectionDiscoveryService([HttpDiscoveryStrategy(httpx.MockTransport(page))], use_assist=False))
    yield
    set_discovery_service(None)


def connect(client, target: str = "http://notebook.lan:8080") -> dict:
    body = client.post("/api/connect/discover", json={"target": target}).json()
    r = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={})
    assert r.status_code == 201, r.text
    return r.json()


def listed(client) -> list[str]:
    return [p["name"] for p in client.get("/api/providers").json()]


def restart(db) -> None:
    """What starting the API, the scheduler and the worker again does to the list."""
    from app.services import reconcile as reconcile_service
    from app.services.runtime import ensure_runtimes

    provider_service.seed_examples(db, demo=False)
    ensure_runtimes(db)
    provider_service.settle_descriptions(db)
    reconcile_service.reconcile_all(db)
    db.commit()


def test_A_an_empty_workspace_lists_nothing(client, db):
    restart(db)
    assert client.get("/api/providers").json() == []


def test_B_support_for_something_is_not_an_entry(client, db):
    restart(db)
    assert "claude_code" in adapter_kinds()  # Bevro can drive it...
    assert listed(client) == []  # ...and it is not in anyone's list for that
    offered = client.get("/api/integrations").json()
    assert [o["slug"] for o in offered] == ["claude-code"]
    # Only choosing it adds it; then it is no longer offered.
    r = client.post("/api/integrations/claude-code")
    assert r.status_code == 201 and r.json()["name"] == "Claude Code"
    assert listed(client) == ["Claude Code"]
    assert client.get("/api/integrations").json() == []
    assert client.post("/api/integrations/claude-code").status_code == 404
    assert client.post("/api/integrations/not-a-thing").status_code == 404


def test_C_connected_appears(client, db, page_at_notebook):
    connect(client)
    assert listed(client) == ["Notebook"]


def test_D_created_appears(client, db):
    r = client.post("/api/create/activate", json={"name": "Digest", "description": "Summarises the week's notes.", "capabilities": [{"id": "summarise", "title": "Summarise"}]})
    assert r.status_code == 201, r.text
    assert listed(client) == ["Digest"]


def test_E_F_G_removed_is_gone_and_stays_gone(client, db, page_at_notebook):
    added = connect(client)
    assert client.delete(f"/api/providers/{added['id']}").status_code == 204
    assert listed(client) == []  # E
    assert client.get(f"/api/providers/{added['id']}").status_code == 404  # F: asking again
    restart(db)  # G: starting everything again brings nothing back
    assert listed(client) == []


def test_G_a_removed_shipped_integration_stays_removed(client, db):
    added = client.post("/api/integrations/claude-code").json()
    assert client.delete(f"/api/providers/{added['id']}").status_code == 204
    restart(db)
    assert listed(client) == []
    # It is offered again, and only that.
    assert [o["slug"] for o in client.get("/api/integrations").json()] == ["claude-code"]


def test_H_removed_can_be_found_and_added_again_as_new(client, db, page_at_notebook):
    first = connect(client)
    client.delete(f"/api/providers/{first['id']}")
    body = client.post("/api/connect/discover", json={"target": "http://notebook.lan:8080"}).json()
    assert body["state"] == "found" and not body.get("already_connected")
    again = connect(client)
    assert again["id"] != first["id"] and again["name"] == "Notebook"
    assert listed(client) == ["Notebook"]


def test_I_J_removal_keeps_history_and_takes_current_setup(client, db):
    from app.services.secrets import SecretStore

    p = provider_service.register_provider(
        db,
        {"name": "Ledger", "description": "", "capabilities": [{"id": "invoices", "title": "Invoices"}], "adapter": {"kind": "http", "config": {"base_url": "http://ledger.lan"}}, "surfaces": [surface.declared_web_surface("https://ledger.example.org")], "origin": "connected"},
    )
    task = Task(title="March totals", original_request="Total for March", state="completed")
    db.add(task)
    db.flush()
    db.add(ProviderRun(task_id=task.id, provider_id=p.id, provider_name="Ledger", provider_slug=p.slug, state="completed"))
    SecretStore().put(db, p.id, "api_key", "value-for-test")
    db.commit()
    assert client.get(f"/api/providers/{p.id}/removal").json()["credentials"] == 1

    assert client.delete(f"/api/providers/{p.id}").status_code == 204
    # J: the item, its credentials and its setup are gone.
    assert db.get(Provider, p.id) is None
    assert db.scalar(select(func.count()).select_from(ProviderSecret).where(ProviderSecret.provider_id == p.id)) == 0
    # I: its work is still there, still saying who did it.
    [kept] = client.get("/api/tasks").json()
    assert kept["title"] == "March totals"
    run = db.scalar(select(ProviderRun).where(ProviderRun.task_id == task.id))
    assert run.provider_id is None and run.provider_name == "Ledger"
    detail = client.get(f"/api/tasks/{task.id}").json()
    assert detail["runs"][0]["provider"]["name"] == "Ledger" and detail["runs"][0]["provider"]["removed"] is True


def test_K_a_web_only_item_can_be_removed(client, db, page_at_notebook):
    added = connect(client)
    assert added["direct"]["state"] == "not_set_up"
    assert client.delete(f"/api/providers/{added['id']}").status_code == 204
    assert listed(client) == []


def test_L_something_bevro_drives_can_be_removed(client, db):
    p = provider_service.register_provider(db, {"name": "Ledger", "description": "", "capabilities": [{"id": "invoices", "title": "Invoices"}], "adapter": {"kind": "http", "config": {"base_url": "http://ledger.lan"}}, "origin": "connected"})
    db.commit()
    assert client.get(f"/api/providers/{p.id}").json()["direct"]["state"] == "ready"
    assert client.delete(f"/api/providers/{p.id}").status_code == 204
    assert listed(client) == []


# The real projects this was accepted against. Their names belong in nobody's
# shipped code, migrations or manifests - not even as an example in a comment.
REAL_NAMES = re.compile(r"\b(zekor|archivist|moimio|career[ _-]?agent)\b", re.IGNORECASE)


def test_M_no_shipped_source_names_a_real_app():
    # The web app's own code is checked by web/src/test/neutrality.test.ts.
    places = [ROOT / "api" / "app", ROOT / "api" / "alembic", ROOT / "adapters", ROOT / "providers"]
    assert all(p.is_dir() for p in places), [str(p) for p in places if not p.is_dir()]
    offenders = []
    for base in places:
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".ts", ".tsx", ".json", ".css", ".html") or "test" in path.parts or "__pycache__" in path.parts:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if REAL_NAMES.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == []
