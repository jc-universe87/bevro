"""A stand-in coding provider for tests: it builds bridges without a model.

Registered like any other provider (a `local` adapter and the capability
`integration_bridge_building`), so the bridge builder finds it by capability
exactly as it would find a real coding agent. CI never calls a paid model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from adapters import ArtifactDraft, InvocationRequest, InvocationResult, ProviderSpec, ResultState

# What the fake does with the work it is given, set by each test.
BEHAVIOUR = {"mode": "python"}

PYTHON_BRIDGE = '''#!/usr/bin/env python3
"""Bevro connection for a project that has no interface of its own."""
import json, sys, importlib
sys.path.insert(0, {source!r})

def main() -> None:
    payload = json.loads(sys.stdin.read() or "{{}}")
    request = payload.get("request", "")
    module = importlib.import_module({module!r})
    answer = getattr(module, {callable!r})(request)
    print(json.dumps({{"status": "completed", "summary": str(answer)[:200],
                      "artifacts": [{{"type": "report", "title": "Answer", "text": str(answer)}}]}}))

if __name__ == "__main__":
    main()
'''

NODE_BRIDGE = '''#!/usr/bin/env node
// Bevro connection for a Node project with no interface of its own.
const path = require("path");
const mod = require(path.join({source!r}, {entry!r}));
let input = "";
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", () => {{
  const payload = input ? JSON.parse(input) : {{}};
  const answer = mod[{callable!r}](payload.request || "");
  process.stdout.write(JSON.stringify({{status: "completed", summary: String(answer).slice(0, 200),
    artifacts: [{{type: "report", title: "Answer", text: String(answer)}}]}}));
}});
'''

_SOURCE = re.compile(r"^The project is at: (.+)$", re.M)
_BRIDGE_DIR = re.compile(r"^Write the bridge here, and nowhere else: (.+)$", re.M)


def run(request: InvocationRequest, provider: ProviderSpec) -> InvocationResult:
    """Read the request the bridge builder wrote, and act on it."""
    mode = BEHAVIOUR.get("mode", "python")
    source = _SOURCE.search(request.request)
    folder = _BRIDGE_DIR.search(request.request)
    if not source or not folder:
        return InvocationResult(state=ResultState.FAILED, error="no project or bridge folder in the request")
    project = Path(source.group(1).strip())
    bridge_dir = Path(request.input.get("workspace", {}).get("path") or folder.group(1).strip())
    bridge_dir.mkdir(parents=True, exist_ok=True)

    if mode == "refuse":
        return InvocationResult(state=ResultState.COMPLETED, summary="I couldn't find anything safe to call in this project.")
    if mode == "touch_source":
        # A misbehaving builder: it writes into the project it was told to leave alone.
        (project / "SCRATCH.txt").write_text("a builder wrote here\n", encoding="utf-8")
    if mode == "broken":
        (bridge_dir / "bridge.py").write_text("import sys\nsys.stdout.write('not json at all')\n", encoding="utf-8")
        _manifest(bridge_dir, ["python3", "bridge.py"], "nothing")
        return InvocationResult(state=ResultState.COMPLETED, summary="Wrote a bridge.")

    evidence = json.loads(request.request.split("(found by reading it, never running it):\n", 1)[1].split("\n\nThe contract", 1)[0])
    if mode == "node":
        exports = evidence.get("exports") or [{"file": "index.js", "exports": ["answer"]}]
        entry, name = exports[0]["file"], exports[0]["exports"][0]
        (bridge_dir / "bridge.js").write_text(NODE_BRIDGE.format(source=str(project), entry=entry, callable=name), encoding="utf-8")
        (bridge_dir / "bridge.py").write_text("# the bridge for this project is bridge.js\n", encoding="utf-8")
        _manifest(bridge_dir, ["node", "bridge.js"], f"{entry}:{name}")
    else:
        modules = evidence.get("modules") or []
        if not modules:
            return InvocationResult(state=ResultState.COMPLETED, summary="Nothing callable was found.")
        module = modules[0]["module"]
        name = modules[0]["callables"][0]["name"]
        source_root = str(project / "src") if evidence.get("package_layout") == "src" else str(project)
        (bridge_dir / "bridge.py").write_text(PYTHON_BRIDGE.format(source=source_root, module=module, callable=name), encoding="utf-8")
        _manifest(bridge_dir, ["python3", "bridge.py"], f"{module}.{name}")
    (bridge_dir / "README.md").write_text("Bevro's connection for this project.\n", encoding="utf-8")
    return InvocationResult(
        state=ResultState.COMPLETED,
        summary="Built a connection and checked that it answers.",
        artifacts=[ArtifactDraft(type="report", title="What was built", mime_type="text/markdown", payload={"text": "A small bridge was written into Bevro's own folder."})],
    )


def _manifest(bridge_dir: Path, command: list[str], called: str) -> None:
    (bridge_dir / "bridge.json").write_text(json.dumps({"entrypoint": "bridge.py", "command": command, "callable": called, "notes": None}, indent=2), encoding="utf-8")


MANIFEST = {
    "slug": "fixture-builder",
    "name": "Fixture Builder",
    "description": "Builds connections for projects that have none (test double).",
    "enabled": True,
    "capabilities": [{"id": "integration_bridge_building", "title": "Build connections"}, {"id": "coding", "title": "Coding"}],
    "adapter": {"kind": "local", "ref": "tests.fake_builder:run", "config": {}},
    "origin": "example",
}
