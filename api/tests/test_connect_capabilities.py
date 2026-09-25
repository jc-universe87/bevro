"""Capability inference is conservative: clear words in, few accurate capabilities out."""

from app.connect.capabilities import capabilities_from_summary, capability_from_tool, infer_capabilities
from app.connect.draft import ProviderDraft


def test_inference_needs_clear_words_and_stays_small():
    caps = infer_capabilities("Market Research & Strategy Agent", "Researches changes in the software market.", weights=(2.0, 2.0))
    assert [c.id for c in caps] == ["research", "market_analysis", "product_strategy"]
    assert infer_capabilities("Thing", "It does stuff.") == []
    # One passing mention in a long README body is not evidence.
    assert infer_capabilities(None, None, "we may translate this one day", weights=(1, 1, 0.34)) == []
    # Generic words need more than a single mention.
    assert infer_capabilities("Notes", None) == []
    many = infer_capabilities("Research competitor markets strategy reports summaries drafts translations emails calendars", weights=(3.0,))
    assert len(many) == 5


def test_summary_typed_by_the_person_becomes_capabilities():
    caps = capabilities_from_summary("Research, Product strategy; weather forecasts\n Research")
    assert [(c.id, c.title) for c in caps] == [("research", "Research"), ("product_strategy", "Product strategy"), ("weather_forecasts", "weather forecasts")]
    assert capabilities_from_summary("") == []


def test_tools_and_operations_keep_their_names():
    cap = capability_from_tool("search_notes", "Search  your\nnotes")
    assert (cap.id, cap.title, cap.description) == ("search_notes", "Search Notes", "Search your notes")


def test_public_view_carries_labels_not_internals():
    draft = ProviderDraft(name="X", mechanism="command", adapter={"kind": "command", "config": {"cwd": "/secret/place"}}, confidence="low", assist_evidence={"dependencies": ["openai"]})
    public = draft.public()
    assert public["confidence_label"] == "Needs review" and public["note"] == "Bevro found this, but couldn't tell what it's for."
    assert "adapter" not in public and "assist_evidence" not in public and "/secret/place" not in str(public)
    assert ProviderDraft(name="Y", mechanism="http", adapter={}, confidence="high").public()["confidence_label"] == "Confident"
