"""Example and built-in providers.

Each provider is a JSON manifest in `manifests/` (what it is, what it can
do, how to reach it) plus, for local ones, a Python handler referenced by
the manifest's adapter `ref`. External providers connected through the UI
have a manifest-shaped record in the database instead.
"""

import json
from pathlib import Path
from typing import Any

MANIFEST_DIR = Path(__file__).parent / "manifests"


def load_manifests() -> list[dict[str, Any]]:
    manifests = []
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as fh:
            manifest = json.load(fh)
        manifest.pop("$comment", None)
        manifests.append(manifest)
    return manifests
