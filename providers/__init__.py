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


# Why a manifest ships, which decides whether a real workspace gets it:
#   "demo"        an example, for tests, screenshots and BEVRO_DEMO_MODE only
#   "integration" a real optional provider, always registered so that work can
#                 be routed to it the moment its runtime is actually there
DEMO = "demo"
INTEGRATION = "integration"


def load_manifests(*, seed: str | None = None) -> list[dict[str, Any]]:
    """The shipped manifests, optionally only those of one kind."""
    manifests = []
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as fh:
            manifest = json.load(fh)
        manifest.pop("$comment", None)
        if seed is not None and manifest.get("seed", INTEGRATION) != seed:
            continue
        manifests.append(manifest)
    return manifests


def demo_slugs() -> set[str]:
    """Slugs Bevro ships purely as examples."""
    return {m["slug"] for m in load_manifests(seed=DEMO)}
