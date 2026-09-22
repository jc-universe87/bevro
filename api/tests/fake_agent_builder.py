"""A stand-in coding provider that builds agents, for tests.

It writes a small, real Python project — metadata, README, tests and a CLI —
into the folder it is given, exactly as a coding agent would. Selected by
capability like any other builder, so CI never calls a paid model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from adapters import ArtifactDraft, InvocationRequest, InvocationResult, ProviderSpec, ResultState

# What the stand-in does with the work, set by each test.
BEHAVIOUR = {"mode": "cli"}

AGENT_SOURCE = '''"""{name}: {description}"""

import argparse
import json
import os


def handle(request: str) -> dict:
    """Do the work for one request."""
    topic = request.strip() or "nothing in particular"
    return {{
        "summary": "{name} looked at: " + topic,
        "findings": ["{capability} on " + topic],
    }}


def main() -> None:
    parser = argparse.ArgumentParser(description="{description}")
    parser.add_argument("--request", required=True, help="What you want it to do, in plain words.")
    parser.add_argument("--format", default="text", choices=["text", "json"])
    arguments = parser.parse_args()
    result = handle(arguments.request)
    if arguments.format == "json":
        print(json.dumps(result))
    else:
        print(result["summary"])
        for line in result["findings"]:
            print("- " + line)


if __name__ == "__main__":
    main()
'''

TEST_SOURCE = '''from {package}.agent import handle


def test_handle_answers_with_a_summary():
    result = handle("widgets")
    assert "widgets" in result["summary"]
    assert result["findings"]
'''

README = """# {name}

{description}

## Run

```bash
python -m {package}.agent --request "your question"
```

## Tests

```bash
python -m pytest -q
```

## Configuration

{config}
"""

_TARGET = re.compile(r"^Write it here, and nowhere else: (.+)$", re.M)
_NAME = re.compile(r"^Build a small, self-contained agent called (.+?)\.$", re.M)
_ABILITIES = re.compile(r"^It should be able to:\n((?:- .*\n)+)", re.M)


def _package_for(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "agent"


def run(request: InvocationRequest, provider: ProviderSpec) -> InvocationResult:
    mode = BEHAVIOUR.get("mode", "cli")
    target = _TARGET.search(request.request)
    name_match = _NAME.search(request.request)
    if not target or not name_match:
        return InvocationResult(state=ResultState.FAILED, error="the build request was not understood")
    folder = Path(request.input.get("workspace", {}).get("path") or target.group(1).strip())
    folder.mkdir(parents=True, exist_ok=True)
    name = name_match.group(1).strip()
    package = _package_for(name)
    abilities = _ABILITIES.search(request.request)
    capability = abilities.group(1).splitlines()[0].lstrip("- ").split(":")[0] if abilities else "Work"
    description = next((line.split(": ", 1)[1] for line in request.request.splitlines() if line.startswith("In one sentence: ")), name)

    if mode == "fail":
        return InvocationResult(state=ResultState.FAILED, error="the builder could not finish")
    if mode == "empty":
        (folder / "notes.txt").write_text("I thought about it.\n", encoding="utf-8")
        return InvocationResult(state=ResultState.COMPLETED, summary="Wrote some notes.")

    source = folder / package
    source.mkdir(parents=True, exist_ok=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    body = AGENT_SOURCE.format(name=name, description=description, capability=capability)
    if mode == "secret":
        body = body.replace('def handle(request: str) -> dict:', 'API_KEY = "sk-live-abcdefghijklmnopqrstuvwxyz123456"\n\n\ndef handle(request: str) -> dict:')
    if mode == "broken_tests":
        body = body.replace('"summary": "{n} looked at: " + topic'.format(n=name), '"summary": "nothing"')
    (source / "agent.py").write_text(body, encoding="utf-8")
    (folder / "pyproject.toml").write_text(
        f'[project]\nname = "{package.replace("_", "-")}"\nversion = "0.1.0"\ndescription = "{description}"\nrequires-python = ">=3.11"\ndependencies = []\n',
        encoding="utf-8",
    )
    (folder / "README.md").write_text(README.format(name=name, description=description, package=package, config="No configuration is needed."), encoding="utf-8")
    tests = folder / "tests"
    tests.mkdir(exist_ok=True)
    (tests / f"test_{package}.py").write_text(TEST_SOURCE.format(package=package), encoding="utf-8")
    (folder / "conftest.py").write_text("import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent))\n", encoding="utf-8")
    return InvocationResult(
        state=ResultState.COMPLETED,
        summary=f"Built {name} and its tests pass.",
        artifacts=[ArtifactDraft(type="report", title="What was built", mime_type="text/markdown", payload={"text": f"A small project for {name}."})],
    )


MANIFEST = {
    "slug": "fixture-agent-builder",
    "name": "Fixture Agent Builder",
    "description": "Builds agents from a description (test double).",
    "enabled": True,
    "capabilities": [{"id": "agent_building", "title": "Build agents"}, {"id": "coding", "title": "Coding"}],
    "adapter": {"kind": "local", "ref": "tests.fake_agent_builder:run", "config": {}},
    "origin": "example",
}
