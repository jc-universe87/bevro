"""Apps & agents: known, ways to use it, and whether Bevro can send it work.

Three questions, kept apart (docs/HUB.md). Every fixture here is synthetic:
the shapes of real things - a web app with no interface for programs, an
agent that posts to a chat, a service that is both - never their names.
"""

from __future__ import annotations

import httpx
import pytest

from adapters.runtime import Reachability
from app.connect import surfaces as surface
from app.connect.compose_ports import bind_scope
from app.connect.probes import shares_from_serve_status
from app.services import providers as provider_service
from app.services import runtime as runtime_service
from app.services.reconcile import direct_access
from tests import operational_fixtures as fx


def _provider(db, name: str, *, runtimes=(), surfaces=(), capabilities=(), description: str = "", app_url: str | None = None):
    provider = provider_service.register_provider(
        db,
        {
            "name": name,
            "description": description,
            "capabilities": list(capabilities),
            "adapter": {},
            "runtimes": list(runtimes),
            "surfaces": list(surfaces),
            "app_url": app_url,
            "origin": "connected",
        },
    )
    db.commit()
    return provider


def _out(client, provider) -> dict:
    return client.get(f"/api/providers/{provider.id}").json()


WEB = surface.web_surface("http://127.0.0.1:6400", bind="all", title="Notebook")


# --------------------------------------------------------------------------- 1-3, 5: the three questions

def test_1_something_bevro_can_send_work_to(client, db):
    p = _provider(db, "Ledger", runtimes=[fx.network_runtime("openapi", api="available", base="http://ledger.local")], capabilities=[{"id": "invoices", "title": "Invoices"}])
    out = _out(client, p)
    assert out["direct"]["state"] == "ready"
    assert out["actions"] == ["ask"]
    assert out["surfaces"] == []


def test_2_a_web_app_with_nothing_for_programs_is_healthy_not_broken(client, db):
    p = _provider(db, "Notebook", surfaces=[WEB], description="Keeps what you have noted and learned.")
    out = _out(client, p)
    assert out["direct"]["state"] == "not_set_up"
    assert out["direct"]["note"] == "Bevro can't send it work directly yet."
    assert "ask" not in out["actions"] and "open" in out["actions"]
    [web] = out["surfaces"]
    assert web["label"] == "Web app" and web["role"] == "use" and web["reach"] == "all_interfaces"
    # Nothing about it is phrased as a fault.
    words = f"{out['direct']['note']} {web['sentence']}".lower()
    assert not any(bad in words for bad in ("can't be reached", "not usable", "no connection", "failed", "error"))


def test_3_a_service_that_is_both_offers_both(client, db):
    app = surface.web_surface("http://ledger.lan:6300/app/", title="Ledger")
    p = _provider(db, "Ledger", runtimes=[fx.network_runtime("openapi", api="available", base="http://ledger.lan:6300")], surfaces=[app], capabilities=[{"id": "invoices", "title": "Invoices"}])
    out = _out(client, p)
    assert out["direct"]["state"] == "ready"
    assert out["actions"] == ["ask", "open"]
    assert out["surfaces"][0]["reach"] == "network"


def test_5_a_way_in_that_stopped_working_is_a_problem_and_no_way_in_is_not(client, db):
    gone = fx.network_runtime("openapi", api="available", base="http://ledger.local")
    gone = gone.model_copy(update={"health": gone.health.after_failure("Couldn't reach it.")})
    broken = _provider(db, "Ledger", runtimes=[gone], surfaces=[WEB], capabilities=[{"id": "invoices", "title": "Invoices"}])
    never = _provider(db, "Notebook", surfaces=[WEB])
    assert direct_access(db, broken)["state"] == "unreachable"
    assert direct_access(db, never)["state"] == "not_set_up"
    # Said as a problem, with the way out being to try again.
    assert _out(client, broken)["direct"]["note"] == "Bevro could send it work before, but can't reach it right now."


def test_a_mirrored_adapter_is_not_a_way_in(client, db):
    """A runtime found but unable to take a task leaves its adapter block on
    the provider. That must not turn into an Ask button."""
    from adapters.runtime import RuntimeKind, RuntimeProfile

    page = RuntimeProfile(id="http", kind=RuntimeKind.HTTP, display_name="Connected over the network", availability="not_invocable", adapter={"kind": "http", "config": {"base_url": "http://page.local"}})
    p = _provider(db, "Page", runtimes=[page], surfaces=[WEB])
    assert p.adapter.get("kind") == "http"
    out = _out(client, p)
    assert "ask" not in out["actions"] and out["direct"]["state"] == "not_set_up"


def test_paused_is_said_as_paused(client, db):
    p = _provider(db, "Ledger", runtimes=[fx.network_runtime("openapi", api="available")], capabilities=[{"id": "x", "title": "X"}])
    client.patch(f"/api/providers/{p.id}", json={"enabled": False})
    assert _out(client, p)["direct"]["state"] == "paused"


# --------------------------------------------------------------------------- 4: other ways to use it

def _bot_sources(receive: bool) -> list[str]:
    code = "import os, requests\nTOKEN = os.environ['TELEGRAM_BOT_TOKEN']\nrequests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage', json={})\n"
    if receive:
        code += "updates = requests.get(f'https://api.telegram.org/bot{TOKEN}/getUpdates').json()\n"
    return [code]


def test_4_a_chat_it_answers_is_a_way_to_use_it():
    [chat] = surface.messaging_surfaces([], _bot_sources(receive=True), [], "")
    assert (chat.kind, chat.role) == ("telegram", "use")
    assert surface.public(chat)["sentence"] == "You can use it through Telegram."


def test_4_a_chat_it_only_posts_to_is_where_results_go_not_a_way_in():
    [chat] = surface.messaging_surfaces([], _bot_sources(receive=False), [], "Sends the weekly briefing to Telegram.")
    assert (chat.kind, chat.role) == ("telegram", "delivers")
    assert surface.public(chat)["sentence"] == "It sends its results to Telegram."
    assert not surface.has_a_way_to_use([chat])


def test_4_one_signal_is_not_enough():
    # The library is listed and nothing else says so: a leftover, a plan, a test.
    assert surface.messaging_surfaces(["slack-sdk"], ["print('hello')"], [], "") == []
    # With its token, it is a finding.
    found = surface.messaging_surfaces(["slack-bolt"], ["app = App()\n@app.command('/ask')\ndef ask(): ..."], ["SLACK_BOT_TOKEN"], "")
    assert [(s.kind, s.role) for s in found] == [("slack", "use")]


def test_4_a_schedule_is_said_in_words(tmp_path):
    from app.connect.inspect import Project
    from app.connect.probes import systemd_timers

    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "digest.timer").write_text("[Timer]\nOnCalendar=Mon *-*-* 07:30:00 UTC\n", encoding="utf-8")
    [timer] = surface.schedule_surfaces(None, systemd_timers(Project(tmp_path), ask_systemd=False))
    assert (timer.kind, timer.role, timer.when) == ("schedule", "runs", "every Monday at 07:30 UTC")
    assert timer.installed is False
    assert surface.public(timer)["sentence"] == "It comes with a schedule that isn't set up on this machine."


@pytest.mark.parametrize(
    "value,words",
    [
        ("daily", "every day"),
        ("*-*-* 06:00", "every day at 06:00"),
        ("Mon,Fri *-*-* 10:00", "every Monday and Friday at 10:00"),
        ("*-01,04,07,10-01 07:00:00 UTC", "four times a year at 07:00 UTC"),
        ("*-*-01 09:00:00", "on day 1 of every month at 09:00"),
        ("some shape nobody writes", None),
    ],
)
def test_schedules_in_words(value, words):
    assert surface.when_in_words(value) == words


# --------------------------------------------------------------------------- 6, 7: where a browser can open it

@pytest.mark.parametrize(
    "entry,scope",
    [("8501:8501", "all"), ("0.0.0.0:80:80", "all"), ("127.0.0.1:6400:80", "loopback"), ("[::1]:80:80", "loopback"), ("192.168.1.5:80:80", "address"), ("${BIND}:80:80", None), ({"published": 80, "target": 80}, "all"), ({"host_ip": "127.0.0.1", "published": 80}, "loopback")],
)
def test_how_a_port_is_published(entry, scope):
    assert bind_scope(entry) == scope


def test_6_7_an_address_is_classified_by_how_it_was_published():
    assert surface.web_surface("http://127.0.0.1:6400", bind="loopback").reach == "loopback"
    assert surface.web_surface("http://127.0.0.1:6400", bind="all").reach == "all_interfaces"
    # Not knowing is treated as loopback: better unoffered than broken.
    assert surface.web_surface("http://127.0.0.1:6400", bind=None).reach == "loopback"
    assert surface.web_surface("http://10.0.0.8:6400").reach == "network"
    assert surface.web_surface("https://notes.example.org").reach == "network"
    assert surface.declared_web_surface("http://127.0.0.1:1").reach == "explicit"


def test_6_a_private_network_share_is_the_address_for_browsers():
    status = {
        "TCP": {"8443": {"HTTPS": True}, "7001": {"TCPForward": "127.0.0.1:7000"}},
        "Web": {"box.example.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:6400"}}}, "box.example.ts.net:443": {"Handlers": {"/wiki": {"Proxy": "http://localhost:5000"}}}},
    }
    shares = shares_from_serve_status(status, lambda: "box.example.ts.net")
    assert shares == {6400: "https://box.example.ts.net:8443", 5000: "https://box.example.ts.net/wiki", 7000: "http://box.example.ts.net:7001"}
    web = surface.web_surface("http://127.0.0.1:6400", bind="loopback", shares=shares)
    assert (web.url, web.reach, web.local_url) == ("https://box.example.ts.net:8443", "shared", "http://127.0.0.1:6400")
    # Something shared elsewhere is nothing to do with a real network address.
    assert surface.web_surface("http://10.0.0.8:6400", shares=shares).reach == "network"


def test_nothing_is_read_from_a_share_pointing_somewhere_else():
    assert shares_from_serve_status({"Web": {"h:443": {"Handlers": {"/": {"Proxy": "http://10.1.1.1:80"}}}}}) == {}
    assert shares_from_serve_status({}) == {} and shares_from_serve_status({"Web": "nonsense"}) == {}


def test_an_address_the_person_gives_wins_and_survives_looking_again(client, db):
    p = _provider(db, "Notebook", surfaces=[surface.web_surface("http://127.0.0.1:6400", bind="loopback")])
    r = client.patch(f"/api/providers/{p.id}", json={"web_address": "https://notes.example.org/"})
    assert r.status_code == 200, r.text
    [web] = r.json()["surfaces"]
    assert (web["url"], web["reach"]) == ("https://notes.example.org/", "explicit")
    # What discovery finds next time is merged under it, not over it.
    given = [x for x in surface.from_stored(p.surfaces) if x.reach == "explicit"]
    merged = surface.merge(given, [surface.web_surface("http://127.0.0.1:6400", bind="loopback")])
    assert surface.for_provider([m.model_dump() for m in merged], None)[0].url == "https://notes.example.org/"
    # And it can be forgotten.
    r = client.patch(f"/api/providers/{p.id}", json={"web_address": ""})
    assert r.json()["surfaces"][0]["reach"] == "loopback"


def test_a_web_address_must_be_one(client, db):
    p = _provider(db, "Notebook", surfaces=[WEB])
    r = client.patch(f"/api/providers/{p.id}", json={"web_address": "javascript:alert(1)"})
    assert r.status_code == 422


# --------------------------------------------------------------------------- what it is for

def test_an_apps_own_screens_say_what_it_is_for(tmp_path):
    from app.connect.inspect import Project
    from app.connect.uicopy import ui_copy

    source = (
        "import streamlit as st\n"
        "st.caption(\"Already saved a letter but can't find it? Search the archive by keyword.\")\n"
        "st.caption(f\"Location: {secret_value}\")\n"
        "st.title('Box')\n"
    )
    (tmp_path / "index.html").write_text('<meta name="description" content="A quiet place to file and find paperwork.">', encoding="utf-8")
    found = ui_copy(Project(tmp_path), [source])
    assert found == ["Already saved a letter but can't find it? Search the archive by keyword.", "A quiet place to file and find paperwork."]
    assert not any("Location" in f for f in found)  # built at run time: not copy


def test_docker_compose_is_not_writing():
    from app.connect.capabilities import infer_capabilities

    readme = "Run it with docker compose up -d. The compose file starts four containers."
    assert "draft" not in [c.id for c in infer_capabilities("Thing", readme)]
    assert "draft" in [c.id for c in infer_capabilities("Thing", "It helps you compose emails and drafts replies.")]


# --------------------------------------------------------------------------- Home: go here instead

def test_home_points_at_the_app_for_it_when_bevro_cannot_do_it(client, db):
    _provider(db, "Notebook", surfaces=[WEB], capabilities=[{"id": "knowledge", "title": "Search what you know"}], description="Keeps what you have noted and learned.")
    r = client.post("/api/tasks", json={"request": "Where did I write about sourdough?"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["reason"] == "use_elsewhere" and detail["suggestion"]["name"] == "Notebook"
    assert "open it" in detail["message"]


def test_naming_the_app_is_enough(client, db):
    _provider(db, "Notebook", surfaces=[WEB])
    detail = client.post("/api/tasks", json={"request": "open notebook"}).json()["detail"]
    assert detail["suggestion"]["name"] == "Notebook"


def test_no_suggestion_without_a_real_match_or_with_a_tie(client, db):
    _provider(db, "Notebook", surfaces=[WEB], capabilities=[{"id": "knowledge", "title": "Search what you know"}])
    _provider(db, "Diary", surfaces=[WEB], capabilities=[{"id": "knowledge", "title": "Search what you know"}])
    assert "suggestion" not in client.post("/api/tasks", json={"request": "Book a table for two"}).json()["detail"]
    assert "suggestion" not in client.post("/api/tasks", json={"request": "Search what I know about bees"}).json()["detail"]


def test_something_that_only_posts_results_is_not_suggested(client, db):
    _provider(db, "Briefing", surfaces=[surface.Surface(kind="telegram", role="delivers")], capabilities=[{"id": "research", "title": "Research"}])
    assert "suggestion" not in client.post("/api/tasks", json={"request": "open briefing"}).json()["detail"]


# --------------------------------------------------------------------------- Connect: adding what it is

def test_connect_adds_a_web_app_as_what_it_is(client, db):
    """A page for people answers; nothing describes a way to send it work.
    Connect adds it to the hub - known, used through its own app - with no
    way in invented for Bevro."""
    from app.connect.service import ConnectionDiscoveryService, set_discovery_service
    from app.connect.strategies.http import HttpDiscoveryStrategy

    def page(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text="<html><title>Notebook</title></html>", headers={"Content-Type": "text/html"})
        return httpx.Response(404)

    set_discovery_service(ConnectionDiscoveryService([HttpDiscoveryStrategy(httpx.MockTransport(page))], use_assist=False))
    try:
        body = client.post("/api/connect/discover", json={"target": "http://notebook.lan:8080"}).json()
        draft = body["draft"]
        assert draft["invocable"] is False and draft["can_add"] is True
        assert [s["kind"] for s in draft["surfaces"]] == ["web_app"]
        r = client.post(f"/api/connect/drafts/{body['id']}/confirm", json={})
        assert r.status_code == 201, r.text
        added = r.json()
        assert added["direct"]["state"] == "not_set_up" and added["actions"] == ["open"]
        assert added["surfaces"][0]["url"] == "http://notebook.lan:8080" and added["surfaces"][0]["reach"] == "network"
        provider = provider_service.get_provider(db, added["id"])
        assert provider.adapter == {} or not any(rt.invocable for rt in runtime_service.runtimes_of(provider))
    finally:
        set_discovery_service(None)


def test_reachability_is_not_browser_reachability():
    """The worker reaching a service and a browser opening it are separate facts."""
    rt = fx.network_runtime("openapi", api="unavailable", worker="available", base="http://127.0.0.1:6300")
    assert rt.reachability == Reachability(api="unavailable", worker="available")
    assert surface.web_surface("http://127.0.0.1:6300", bind="loopback").reach == "loopback"


def test_an_agents_own_words_beat_a_general_keyword_family():
    """"Review my job openings" is one agent's own vocabulary, not a request
    for research in general - even when a research agent is also there."""
    from app.routing.catalogue import CatalogueCapability, CatalogueEntry
    from app.routing.deterministic import DeterministicRouter

    def entry(slug, caps):
        return CatalogueEntry(id=slug, name=slug.title(), description="", capabilities=[CatalogueCapability(id=c.lower().replace(" ", "_"), title=c) for c in caps], available=True, can_invoke=True)

    researcher = entry("analyst", ["Research", "Market analysis"])
    openings = entry("openings", ["Review openings", "Prepare documents"])
    decision = DeterministicRouter().decide("Help me review my current job openings", [researcher, openings])
    assert decision.selected_provider_ids == ["openings"]
    # A plain research request still goes by the rule.
    assert DeterministicRouter().decide("Research the market for bikes", [researcher, openings]).selected_provider_ids == ["analyst"]


def test_a_filing_question_points_at_the_filing_app(client, db):
    _provider(db, "Cabinet", surfaces=[WEB], capabilities=[{"id": "archive", "title": "Filing and finding documents"}])
    detail = client.post("/api/tasks", json={"request": "Where is my passport filed?"}).json()["detail"]
    assert detail["suggestion"]["name"] == "Cabinet"
