"""The generic execution path is data-driven: nothing branches on a provider's name.

Adapters implement mechanisms (a CLI tool is one adapter kind, not a special
provider); example manifests are data; comments may say what they like.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
ADAPTERS = Path(__file__).resolve().parents[2] / "adapters"
NAMES = re.compile(r"\b(moimio|career[-_ ]?agent|anthropic|openai)\b", re.IGNORECASE)
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
            if re.search(r"\b(moimio|career[-_ ]?agent)\b", line, re.IGNORECASE):
                offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == [], offenders


def test_runtime_kinds_are_not_provider_types():
    from adapters.runtime import ADAPTER_FOR_KIND, RuntimeKind

    assert set(ADAPTER_FOR_KIND) == {k.value for k in RuntimeKind}
    from app.models import Provider

    assert not hasattr(Provider, "type") and not hasattr(Provider, "provider_type")
