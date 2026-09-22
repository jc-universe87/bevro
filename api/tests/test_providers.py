from adapters import ProviderSpec, get_adapter
from adapters.base import NotSupported
from adapters.registry import adapter_kinds
from app.schemas.serialise import provider_actions
from app.services import providers as provider_service


def test_seed_examples_is_idempotent(db):
    assert provider_service.seed_examples(db) == 3
    assert provider_service.seed_examples(db) == 0
    slugs = {p.slug for p in provider_service.list_providers(db)}
    assert slugs == {"moimio", "research", "claude-code"}


def test_register_provider_with_free_form_capabilities(db):
    provider = provider_service.register_provider(
        db,
        {
            "name": "Sales Desk",
            "description": "Quotes and follow-ups",
            "capabilities": [{"id": "quote", "title": "Quote"}, {"id": "something.new", "title": "Not yet conceived"}],
            "adapter": {"kind": "http", "config": {"base_url": "http://sales.local"}},
        },
    )
    assert provider.slug == "sales-desk"
    assert [c["id"] for c in provider.capabilities] == ["quote", "something.new"]
    assert provider.enabled is True


def test_slug_collisions_are_resolved(db):
    a = provider_service.register_provider(db, {"name": "Twin", "adapter": {"kind": "mcp"}})
    b = provider_service.register_provider(db, {"name": "Twin", "adapter": {"kind": "mcp"}})
    assert a.slug == "twin" and b.slug == "twin-2"


def test_actions_only_list_what_is_supported(seeded):
    moimio = provider_service.get_by_slug(seeded, "moimio")
    research = provider_service.get_by_slug(seeded, "research")
    assert provider_actions(moimio) == ["ask", "open"]
    assert provider_actions(research) == ["ask"]
    moimio.enabled = False
    assert provider_actions(moimio) == ["open"]


def test_declared_provider_has_no_ask_action(db):
    declared = provider_service.register_provider(db, {"name": "Later", "adapter": {"kind": "declared"}})
    assert provider_actions(declared) == []


def test_adapter_registry_knows_the_three_transports():
    assert {"local", "http", "mcp"} <= set(adapter_kinds())
    try:
        get_adapter("carrier-pigeon")
    except NotSupported:
        pass
    else:
        raise AssertionError("unknown kinds must raise NotSupported")


def test_local_adapter_health_check_validates_ref():
    adapter = get_adapter("local")
    good = ProviderSpec(id="1", slug="r", name="R", adapter={"kind": "local", "ref": "providers.research:run"})
    bad = ProviderSpec(id="2", slug="b", name="B", adapter={"kind": "local", "ref": "providers.nothing:run"})
    assert adapter.check(good, {}).ok is True
    assert adapter.check(bad, {}).ok is False
