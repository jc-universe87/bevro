"""The generic execution path is data-driven: nothing branches on a provider's name.

Adapters implement mechanisms (a CLI tool is one adapter kind, not a special
provider); example manifests are data; comments may say what they like.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
ADAPTERS = Path(__file__).resolve().parents[2] / "adapters"
NAMES = re.compile(r"\b(event[-_ ]?demo|career[-_ ]?agent|anthropic|openai)\b", re.IGNORECASE)
# Places where a vendor or agent name is legitimately data or a mechanism:
ALLOWED = {
    "routing/models/anthropic.py",  # a routing backend
    "routing/models/openai.py",
    "routing/models/__init__.py",
    "connect/strategies/project.py",  # dependency name -> credential name table (data)
    "connect/draft.py",  # credential labels (data)
    "services/providers.py",  # credential labels (data)
    "connect/assist.py",  # the prompt's example capability ids
}


def _code_lines(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0]
        if stripped.strip().startswith(('"""', "'''")) or not stripped.strip():
            continue
        yield stripped


def test_no_runtime_logic_branches_on_a_provider_name():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        rel = str(path.relative_to(APP))
        if rel in ALLOWED:
            continue
        in_doc = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.count('"""') % 2 == 1:
                in_doc = not in_doc
                continue
            if in_doc:
                continue
            code = line.split("#", 1)[0]
            if NAMES.search(code) and ("if " in code or "==" in code or "slug" in code or "in (" in code):
                offenders.append(f"{rel}: {line.strip()}")
    assert offenders == [], offenders


def test_adapters_never_name_a_provider():
    offenders = []
    for path in sorted(ADAPTERS.glob("*.py")):
        for line in _code_lines(path):
            if re.search(r"\b(event[-_ ]?demo|career[-_ ]?agent)\b", line, re.IGNORECASE):
                offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == [], offenders


# A service's own routes and addresses arrive at runtime, from what the
# service publishes about itself. Written down in code they would be an
# integration with one product wearing generic clothes. Bevro's own
# conventions (/health, /openapi.json, the loopback probe) are not that.
SERVICE_ROUTE = re.compile(r"[\"']/(?:api/v\d|p)/")
ADDRESS = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
OWN_ADDRESSES = {"127.0.0.1", "0.0.0.0", "255.255.255.255", "1.2.3.4"}


def _without_comments_or_docstrings(path: Path):
    """Code lines only: a docstring may say whatever explains the thing."""
    in_doc = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.count('"""') % 2 == 1:
            in_doc = not in_doc
            continue
        if in_doc:
            continue
        code = line.split("#", 1)[0]
        if code.strip():
            yield code


def test_no_service_specific_route_or_address_is_written_down():
    """Where a service keeps things, and where it lives, is data. This is what
    keeps "works with the thing I connected" from quietly becoming "works with
    one particular product"."""
    offenders = []
    for path in [*sorted(APP.rglob("*.py")), *sorted(ADAPTERS.glob("*.py"))]:
        for line in _without_comments_or_docstrings(path):
            if SERVICE_ROUTE.search(line):
                offenders.append(f"{path.name}: {line.strip()}")
            for found in ADDRESS.findall(line):
                if found not in OWN_ADDRESSES:
                    offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == [], offenders


def test_runtime_kinds_are_not_provider_types():
    from adapters.runtime import ADAPTER_FOR_KIND, RuntimeKind

    assert set(ADAPTER_FOR_KIND) == {k.value for k in RuntimeKind}
    from app.models import Provider

    assert not hasattr(Provider, "type") and not hasattr(Provider, "provider_type")
