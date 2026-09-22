"""What leaves Bevro when it tells you something.

Small and stable, so anything receiving it keeps working:

    {"event": "automation.matched", "title": …, "summary": …, "reason": …,
     "task_id": …, "automation_id": …, "created_at": …, "url": …}

The summary is a few sentences a person would recognise. Whatever a provider
produced is only ever quoted, never forwarded wholesale: the full result
stays in Bevro, behind the link.
"""

from __future__ import annotations

import re

SUMMARY_MAX = 600

# Things that must not travel, even if a provider put them in its answer.
_URL = re.compile(r"https?://\S+")
_UNIX_PATH = re.compile(r"(?<![\w~])(?:/[\w.@+-]+){2,}/?")
_HOME_PATH = re.compile(r"(?<![\w])~(?:/[\w.@+-]+)+/?")
_WINDOWS_PATH = re.compile(r"(?<![\w])[A-Za-z]:\\[^\s\"']+")
_TOKEN = re.compile(r"\b(?:sk|rk|pk|ghp|gho|ghs|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b", re.I)
_OPAQUE = re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}\b")
_ASSIGNED_SECRET = re.compile(r"\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASS|CREDENTIAL)S?)\s*[:=]\s*\S+")


def scrub(text: str | None, *, limit: int = SUMMARY_MAX) -> str | None:
    """A summary fit to send: no paths, no keys, no walls of output."""
    if not text:
        return None
    kept: list[str] = []

    def park(match: re.Match[str]) -> str:
        kept.append(match.group(0))
        return f"\x00{len(kept) - 1}\x00"

    # A web address is not a filesystem path; keep it whole while the rest is cleaned.
    cleaned = _URL.sub(park, text)
    cleaned = _ASSIGNED_SECRET.sub(r"\1: …", cleaned)
    cleaned = _TOKEN.sub("…", cleaned)
    cleaned = _WINDOWS_PATH.sub("…", cleaned)
    cleaned = _HOME_PATH.sub("…", cleaned)
    cleaned = _UNIX_PATH.sub("…", cleaned)
    cleaned = _OPAQUE.sub("…", cleaned)
    cleaned = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rsplit(" ", 1)[0] + "…"
    return cleaned or None


def event_payload(event, *, app_url: str = "") -> dict:
    """The event as anything outside Bevro sees it."""
    link = f"{app_url.rstrip('/')}/notifications" if app_url else None
    if app_url and event.task_id:
        link = f"{app_url.rstrip('/')}/tasks/{event.task_id}"
    return {
        "event": event.kind,
        "title": scrub(event.title, limit=200) or "Bevro",
        "summary": scrub(event.summary),
        "reason": scrub(event.reason, limit=200),
        "task_id": str(event.task_id) if event.task_id else None,
        "automation_id": str(event.automation_id) if event.automation_id else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "url": link,
    }
