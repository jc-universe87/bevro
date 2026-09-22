"""Example Research provider.

It does not search anything yet; it exists to prove the task, run and
artifact plumbing end to end. The pause is deliberate so that the interface
has real intermediate states to show.
"""

from __future__ import annotations

import time

from adapters.base import ArtifactDraft, InvocationRequest, InvocationResult, ProviderSpec, ResultState

WORK_SECONDS = 2.0


def _title_from(request: str) -> str:
    text = " ".join(request.split())
    return text[:1].upper() + text[1:] if text else "Research"


def run(request: InvocationRequest, provider: ProviderSpec) -> InvocationResult:
    time.sleep(WORK_SECONDS if not request.input.get("fast") else 0)
    topic = _title_from(request.request)
    body = (
        f"# {topic}\n\n"
        "This is an example result from the built-in Research provider. "
        "Once a real research provider is connected, its findings will appear here "
        "in the same place, in the same shape.\n\n"
        "## What was asked\n\n"
        f"{request.request}\n\n"
        "## Next steps\n\n"
        "- Connect a research-capable provider under Connect.\n"
        "- Ask again; Bevro will hand the request to it.\n"
    )
    return InvocationResult(
        state=ResultState.COMPLETED,
        summary="Done. Example findings are ready.",
        artifacts=[
            ArtifactDraft(
                type="note",
                title=f"Findings: {topic}"[:200],
                summary="Example findings note",
                mime_type="text/markdown",
                payload={"text": body},
                content=body.encode("utf-8"),
                filename="findings.md",
            )
        ],
    )
