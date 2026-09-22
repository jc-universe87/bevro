"""Opt-in: routes the standard example prompts through the configured real
routing model. Costs a few small requests. Skipped unless
BEVRO_RUN_ROUTER_INTEGRATION=1 and BEVRO_ROUTER_API_KEY is set.

    BEVRO_RUN_ROUTER_INTEGRATION=1 BEVRO_ROUTER_BACKEND=openai BEVRO_ROUTER_API_KEY=... \
      docker compose exec api sh -c 'cd /srv/api && pytest -q -s tests/test_routing_integration.py'
"""

from __future__ import annotations

import os
import time

import pytest

from adapters import HealthResult
from app.config import get_settings
from app.models import Workspace
from app.routing.catalogue import build_catalogue
from app.routing.models import get_routing_model
from app.services import providers as provider_service

pytestmark = pytest.mark.skipif(
    os.environ.get("BEVRO_RUN_ROUTER_INTEGRATION") != "1" or not os.environ.get("BEVRO_ROUTER_API_KEY"),
    reason="set BEVRO_RUN_ROUTER_INTEGRATION=1 and BEVRO_ROUTER_API_KEY to run against the real routing model",
)

CASES = [
    ("Research the differences between PostgreSQL and MariaDB for this project.", "research", False),
    ("Fix the spacing on the Recent page and run the frontend tests.", "claude-code", True),
    ("Allocate the participants for the retreat.", "moimio", False),
    ("Write a poem about autumn.", None, False),
    ("Fix the bug.", "claude-code", True),
    ("Compare approaches for implementing MCP support.", "research", False),
]


def test_real_routing_model_on_the_example_prompts(seeded, tmp_path):
    provider_service.record_availability(seeded, provider_service.get_by_slug(seeded, "claude-code"), HealthResult(ok=True, state="available"))
    for name in ("Alpha", "Beta"):
        d = tmp_path / name.lower()
        d.mkdir()
        seeded.add(Workspace(slug=name.lower(), name=name, path=str(d), permissions=["read", "write"]))
    seeded.flush()
    get_settings.cache_clear()
    model = get_routing_model()
    catalogue = build_catalogue(seeded)
    failures = []
    for text, expected, needs_input in CASES:
        started = time.monotonic()
        decision = model.route(text, catalogue, {})
        elapsed = time.monotonic() - started
        print(f"\n{elapsed:5.2f}s  {text!r}\n        -> {decision.selected_provider_ids} needs_input={decision.needs_input} conf={decision.confidence} {decision.rationale or decision.reason}")
        if decision.provider_id != expected or (needs_input and not decision.needs_input):
            failures.append((text, decision.selected_provider_ids, decision.needs_input))
    ambiguous = model.route("Look into the login problem.", catalogue, {})
    print(f"\nambiguous -> {ambiguous.selected_provider_ids} needs_input={ambiguous.needs_input} {ambiguous.rationale}")
    assert ambiguous.provider_id in {"research", "claude-code"}
    assert not failures, failures
