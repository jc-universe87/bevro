"""Example provider: a specialist application that already exists.

Not every provider is an AI agent. This one stands for the events app, the
rota system or the finance tool a team already runs: it answers with a
concise outcome and a deep link back into its own interface, where the real
detail lives.

The numbers are sample data and nothing is called. The deep-link handling
(app_url + a configured path -> an external_url artifact) is exactly what a
real connected application uses.
"""

from __future__ import annotations

import time

from adapters.base import ArtifactDraft, InvocationRequest, InvocationResult, ProviderSpec, ResultState

WORK_SECONDS = 2.5
SAMPLE_EVENT_ID = "spring-conference"
SAMPLE = {"allocated": 148, "review": 7, "groups": 12}


def review_url(provider: ProviderSpec, event_id: str) -> str | None:
    if not provider.app_url:
        return None
    path = provider.adapter_config.get("review_path", "/events/{event_id}")
    return provider.app_url.rstrip("/") + path.format(event_id=event_id)


def run(request: InvocationRequest, provider: ProviderSpec) -> InvocationResult:
    time.sleep(WORK_SECONDS if not request.input.get("fast") else 0)
    event_id = str(request.input.get("event") or SAMPLE_EVENT_ID)
    allocated, review = SAMPLE["allocated"], SAMPLE["review"]
    summary = f"Done. {allocated} participants allocated. {review} need review."

    artifacts = [
        ArtifactDraft(
            type="structured",
            title="Allocation summary",
            summary=f"{allocated} allocated across {SAMPLE['groups']} groups",
            payload={
                "columns": ["Outcome", "Count"],
                "rows": [
                    ["Allocated", allocated],
                    ["Need review", review],
                    ["Groups", SAMPLE["groups"]],
                ],
            },
            metadata={"event_id": event_id},
        )
    ]
    link = review_url(provider, event_id)
    if link:
        artifacts.append(
            ArtifactDraft(
                type="deep_link",
                title="Review in the events app",
                summary=f"{review} participants need a decision",
                external_url=link,
                metadata={"event_id": event_id, "provider": provider.slug},
            )
        )
    return InvocationResult(state=ResultState.COMPLETED, summary=summary, artifacts=artifacts)
