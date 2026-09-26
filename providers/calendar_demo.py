"""Example provider: an application that already exists.

Not every provider is an AI agent. This one stands for the calendar, the
task board or the finance tool someone already uses: it answers with a
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
SAMPLE_WEEK_ID = "this-week"
SAMPLE = {"moved": 3, "reply": 1, "focus_hours": 4}


def review_url(provider: ProviderSpec, week_id: str) -> str | None:
    if not provider.app_url:
        return None
    path = provider.adapter_config.get("review_path", "/week/{week_id}")
    return provider.app_url.rstrip("/") + path.format(week_id=week_id)


def run(request: InvocationRequest, provider: ProviderSpec) -> InvocationResult:
    time.sleep(WORK_SECONDS if not request.input.get("fast") else 0)
    week_id = str(request.input.get("week") or SAMPLE_WEEK_ID)
    moved, reply = SAMPLE["moved"], SAMPLE["reply"]
    summary = f"Done. {moved} meetings moved. {reply} needs your reply."

    artifacts = [
        ArtifactDraft(
            type="structured",
            title="Your week",
            summary=f"{moved} meetings moved, {SAMPLE['focus_hours']} hours of focus time added",
            payload={
                "columns": ["Change", "Count"],
                "rows": [
                    ["Meetings moved", moved],
                    ["Waiting for your reply", reply],
                    ["Focus hours added", SAMPLE["focus_hours"]],
                ],
            },
            metadata={"week_id": week_id},
        )
    ]
    link = review_url(provider, week_id)
    if link:
        artifacts.append(
            ArtifactDraft(
                type="deep_link",
                title="Open in Calendar",
                summary=f"{reply} invitation needs your reply",
                external_url=link,
                metadata={"week_id": week_id, "provider": provider.slug},
            )
        )
    return InvocationResult(state=ResultState.COMPLETED, summary=summary, artifacts=artifacts)
