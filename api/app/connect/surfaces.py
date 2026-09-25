"""The ways a person meets an app or agent, found from what it already has.

A *surface* is one way of using something, or one place its results go: its
own web app, a chat bot, a schedule it runs on, a command line. Bevro sending
it work is a separate question (its runtimes answer that), and so is whether
Bevro knows what it is for. See docs/HUB.md.

Everything here reads evidence and returns evidence. Nothing is run, nothing
is written, and nothing names a particular project: a Telegram bot is found
by what a Telegram bot is made of, whatever it is called.

Two rules keep this honest:

- One signal is not enough where one signal is ambiguous. A messaging
  library in the dependencies might be a leftover; the library *and* the
  token it needs, or the library and the README saying so, is a finding.
- "It posts there" is not "you use it there". A program that only sends to a
  chat is a destination for its results, not a way to talk to it.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

Kind = Literal["web_app", "telegram", "slack", "discord", "schedule", "command_line"]
# use: the person uses it there. delivers: it sends its results there.
# runs: it does its work there on its own (a schedule).
Role = Literal["use", "delivers", "runs"]
# How a web address was published, which decides where a browser can open it
# from (docs/HUB.md): explicit and shared addresses are meant for browsers;
# network addresses are real ones; the last two are this machine's.
Reach = Literal["explicit", "shared", "network", "all_interfaces", "loopback"]

LABELS: dict[str, str] = {
    "web_app": "Web app",
    "telegram": "Telegram",
    "slack": "Slack",
    "discord": "Discord",
    "schedule": "Scheduled runs",
    "command_line": "Command line",
}


class Surface(BaseModel):
    kind: Kind
    role: Role = "use"
    confidence: Literal["high", "medium"] = "medium"
    # Why Bevro thinks so, one fact per line. Shown under Advanced details.
    evidence: list[str] = Field(default_factory=list, max_length=8)
    # web_app: where it answered, how that address was published, what the
    # page calls itself, and the address on this machine behind a share.
    url: str | None = None
    reach: Reach | None = None
    title: str | None = None
    local_url: str | None = None
    # schedule: when, in words, and whether this machine actually runs it.
    when: str | None = None
    installed: bool | None = None


# --------------------------------------------------------------------------- web apps

LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


def is_loopback_host(host: str | None) -> bool:
    host = (host or "").strip("[]").lower()
    if host in LOOPBACK_NAMES or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def reach_of(url: str, bind: str | None = None) -> Reach:
    """How a found address was published.

    `bind` is what the project's own configuration says about the port:
    "all" (every interface), "loopback", "address" (one specific address, in
    which case the url already carries it) or None when it cannot be known.
    Not knowing is treated as loopback: a link that works only here is
    better left unoffered than offered and broken.
    """
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname
    if not is_loopback_host(host):
        return "network"
    return "all_interfaces" if bind == "all" else "loopback"


# What a page is called when its framework named it and nobody changed it.
FRAMEWORK_TITLES = frozenset({"streamlit", "gradio", "react app", "vite app", "vite + react", "vite + react + ts", "vue app", "svelte app", "next.js", "document", "index", "home"})


def web_surface(url: str, *, bind: str | None = None, title: str | None = None, evidence: list[str] | None = None, shares: dict[int, str] | None = None, explicit: bool = False) -> Surface:
    """A web app found answering at `url`, and the best address for a browser.

    A private-network share of the same port on this machine (`shares`,
    port -> address) is an address made for browsers, so it wins over the
    machine-local one, which is kept alongside.
    """
    from urllib.parse import urlsplit

    lines = list(evidence or [])
    if title and title.strip().lower() in FRAMEWORK_TITLES:
        title = None  # the framework's name, not the app's
    if explicit:
        return Surface(kind="web_app", url=url, reach="explicit", title=title, confidence="high", evidence=lines or ["Its address was given to Bevro"])
    reach = reach_of(url, bind)
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    shared = (shares or {}).get(port) if reach in ("loopback", "all_interfaces") else None
    if shared:
        lines.append("It is shared on your private network from this machine")
        return Surface(kind="web_app", url=shared, reach="shared", title=title, local_url=url, confidence="high", evidence=lines[:8])
    return Surface(kind="web_app", url=url, reach=reach, title=title, confidence="high", evidence=lines[:8])


def declared_web_surface(app_url: str) -> Surface:
    """An address the person gave, or the provider declares for itself."""
    return Surface(kind="web_app", url=app_url, reach="explicit", confidence="high", evidence=["Its address was given to Bevro"])


# --------------------------------------------------------------------------- messaging

@dataclass(frozen=True)
class _Platform:
    kind: str
    # Dependency names (lower-case) that mean the platform's SDK.
    packages: tuple[str, ...]
    # Addresses a program calls when it talks to the platform without an SDK.
    api_hosts: tuple[str, ...]
    token: re.Pattern[str]
    # Receiving: a bot framework's handlers, polling, webhooks for incoming messages.
    inbound: re.Pattern[str]
    # Sending only.
    outbound: re.Pattern[str]
    # Libraries that exist to receive messages: using one is itself inbound evidence.
    bot_frameworks: tuple[str, ...] = ()


PLATFORMS: tuple[_Platform, ...] = (
    _Platform(
        kind="telegram",
        packages=("python-telegram-bot", "aiogram", "pytelegrambotapi", "telethon", "pyrogram", "telegraf", "grammy", "node-telegram-bot-api"),
        api_hosts=("api.telegram.org",),
        token=re.compile(r"\bTELEGRAM_[A-Z0-9_]*(TOKEN|KEY)\b|\bTELEGRAM_BOT\w*\b"),
        inbound=re.compile(r"getUpdates|setWebhook|CommandHandler|MessageHandler|run_polling|start_polling|\.message_handler\(|@dp\.message|bot\.on\(|bot\.command\("),
        outbound=re.compile(r"sendMessage|sendDocument|sendPhoto|send_message\(|send_document\("),
        bot_frameworks=("aiogram", "telegraf", "grammy", "pytelegrambotapi"),
    ),
    _Platform(
        kind="slack",
        packages=("slack-bolt", "slack_bolt", "slack-sdk", "slack_sdk", "@slack/bolt", "@slack/web-api"),
        api_hosts=("hooks.slack.com", "slack.com/api"),
        token=re.compile(r"\bSLACK_[A-Z0-9_]*(TOKEN|SECRET|WEBHOOK[A-Z_]*)\b"),
        inbound=re.compile(r"SocketModeHandler|@app\.(event|command|message|shortcut)\(|app\.(event|command|message)\(|slack_bolt"),
        outbound=re.compile(r"chat_postMessage|chat\.postMessage|hooks\.slack\.com|files_upload"),
        bot_frameworks=("slack-bolt", "slack_bolt", "@slack/bolt"),
    ),
    _Platform(
        kind="discord",
        packages=("discord.py", "discord", "discord.js", "nextcord", "py-cord", "hikari", "disnake"),
        api_hosts=("discord.com/api", "discordapp.com/api"),
        token=re.compile(r"\bDISCORD_[A-Z0-9_]*(TOKEN|WEBHOOK[A-Z_]*)\b"),
        inbound=re.compile(r"on_message|@bot\.command|commands\.Bot\(|messageCreate|slash_command|interactionCreate"),
        outbound=re.compile(r"/api/webhooks/|Webhook\.from_url|WebhookClient"),
        bot_frameworks=("discord.py", "discord.js", "nextcord", "py-cord", "hikari", "disnake"),
    ),
)

SOURCE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts")
MAX_SOURCE_FILES = 120
MAX_SOURCE_DEPTH = 3
MAX_SOURCE_BYTES = 64_000


def source_texts(project: Any) -> list[str]:
    """The project's own source, a bounded amount of it, read the safe way.

    Breadth first from the root, never into dependency or build folders
    (Project.listdir skips those), at most a few levels deep and a fixed
    number of files. Enough to see what a program is made of; not enough to
    turn discovery into reading someone's whole codebase.
    """
    texts: list[str] = []
    queue: list[tuple[str, int]] = [(".", 0)]
    while queue and len(texts) < MAX_SOURCE_FILES:
        rel, depth = queue.pop(0)
        for name in project.listdir(rel):
            path = name if rel == "." else f"{rel}/{name}"
            if name.startswith(".") or name in ("tests", "test", "docs", "examples"):
                continue
            if name.endswith(SOURCE_SUFFIXES):
                text = project.read_text(path, MAX_SOURCE_BYTES)
                if text:
                    texts.append(text)
                if len(texts) >= MAX_SOURCE_FILES:
                    break
            elif depth < MAX_SOURCE_DEPTH and project.is_dir(path):
                queue.append((path, depth + 1))
    return texts


def messaging_surfaces(dependencies: list[str], sources: list[str], env_names: list[str], readme: str) -> list[Surface]:
    """Chat platforms this project talks to, and whether you talk back.

    Found only with two independent signals: the platform's library or API
    in the code, *and* either the token it needs or the README saying so.
    """
    deps = {d.lower() for d in dependencies}
    code = "\n".join(sources)
    readme_lower = (readme or "").lower()
    names = " ".join(env_names)
    found: list[Surface] = []
    for p in PLATFORMS:
        sdk = sorted(deps & set(p.packages))
        calls_api = any(host in code for host in p.api_hosts)
        if not sdk and not calls_api:
            continue
        token = bool(p.token.search(names) or p.token.search(code))
        mentioned = p.kind in readme_lower
        if not (token or mentioned):
            continue  # one signal: could be anything, a leftover, a test
        label = LABELS[p.kind]
        evidence = [f"Uses {label}'s " + ("library (" + ", ".join(sdk[:2]) + ")" if sdk else "API")]
        if token:
            evidence.append(f"Needs a {label} token")
        if mentioned:
            evidence.append(f"Its README mentions {label}")
        receives = bool(p.inbound.search(code)) or any(f in deps for f in p.bot_frameworks)
        sends = bool(p.outbound.search(code))
        if receives:
            role: Role = "use"
            evidence.append("It answers messages")
        elif sends:
            role = "delivers"
            evidence.append("It sends messages; nothing in it reads them")
        else:
            continue  # can't tell which way it goes; saying nothing is better than guessing
        found.append(Surface(kind=p.kind, role=role, confidence="high" if token and mentioned else "medium", evidence=evidence))  # type: ignore[arg-type]
    return found


# --------------------------------------------------------------------------- schedules

_DAYS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
_SHORTHAND = {"minutely": "every minute", "hourly": "every hour", "daily": "every day", "weekly": "every week", "monthly": "every month", "quarterly": "every quarter", "yearly": "every year", "annually": "every year", "semiannually": "twice a year"}


def when_in_words(on_calendar: str) -> str | None:
    """A systemd OnCalendar= value, said the way a person would.

    The common shapes only; anything else is "on a schedule", which is true
    and does not pretend to be more.
    """
    value = " ".join(on_calendar.split())
    if not value:
        return None
    if value.lower() in _SHORTHAND:
        return _SHORTHAND[value.lower()]
    parts = value.split(" ")
    zone = ""
    if len(parts) > 1 and re.fullmatch(r"[A-Za-z]+(/[A-Za-z_]+)*", parts[-1]):
        zone = " " + parts.pop()  # "UTC", "Europe/London": said, not converted
    day = None
    if parts and re.fullmatch(r"[A-Za-z]{3}([,.][A-Za-z]{3})*", parts[0]):
        days = [_DAYS.get(d.lower()) for d in re.split(r"[,.]+", parts[0])]
        if all(days):
            day = " and ".join(d for d in days if d)
        parts = parts[1:]
    if len(parts) > 2 or any("-" not in x and ":" not in x for x in parts):
        return None  # a shape not worth guessing at
    date = parts[0] if parts and "-" in parts[0] else "*-*-*"
    clock = parts[-1] if parts and ":" in parts[-1] else None
    at = f" at {clock[:5]}{zone}" if clock and re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", clock) else ""
    m = re.fullmatch(r"(\*|\d{4})-([\d,*./]+)-([\d*,]+)", date)
    if not m:
        return None
    month, dom = m.group(2), m.group(3)
    if day and month == "*" and dom == "*":
        return f"every {day}{at}"
    if month == "*" and dom == "*":
        return f"every day{at}"
    if month == "*" and dom.isdigit():
        return f"on day {int(dom)} of every month{at}"
    months = [x for x in re.split(r"[,]", month) if x]
    if dom.isdigit() and len(months) == 4:
        return f"four times a year{at}"
    if month.endswith("/3") or "/3" in month:
        return f"every quarter{at}"
    return None


def schedule_surfaces(project: Any, timers: list[Any]) -> list[Surface]:
    """Timers the project ships: work it does on its own, on a schedule."""
    out: list[Surface] = []
    for timer in timers:
        when = timer.when
        state = "set up on this machine" if timer.installed else "shipped with the project, not set up on this machine"
        out.append(
            Surface(
                kind="schedule",
                role="runs",
                confidence="high" if timer.installed else "medium",
                when=when_in_words(when) if when else None,
                installed=timer.installed,
                evidence=[f"A systemd timer ({timer.name}) runs it" + (f", {when_in_words(when)}" if when and when_in_words(when) else "") + f"; {state}"],
            )
        )
    return out


# --------------------------------------------------------------------------- putting it together

ORDER = {"web_app": 0, "telegram": 1, "slack": 2, "discord": 3, "schedule": 4, "command_line": 5}


def merge(*groups: list[Surface]) -> list[Surface]:
    """One of each kind and role, most useful first. Schedules keep each timer."""
    seen: set[tuple[str, str, str | None]] = set()
    out: list[Surface] = []
    for group in groups:
        for s in group:
            key = (s.kind, s.role, s.when if s.kind == "schedule" else "given" if s.reach == "explicit" else None)
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
    return sorted(out, key=lambda s: ORDER.get(s.kind, 9))


def from_stored(items: list[dict[str, Any]] | None) -> list[Surface]:
    out: list[Surface] = []
    for item in items or []:
        try:
            out.append(Surface.model_validate(item))
        except ValueError:
            continue  # written by a newer Bevro, or damaged: skipped, not fatal
    return out


def for_provider(stored: list[dict[str, Any]] | None, app_url: str | None) -> list[Surface]:
    """What a provider's surfaces are now, with one web app at most.

    An address the person gave comes first, then one discovery found, then
    the provider's own `app_url` (declared, or the address it was connected
    from), classified like any other address rather than trusted blindly.
    """
    found = from_stored(stored)
    web = [s for s in found if s.kind == "web_app"]
    rest = [s for s in found if s.kind != "web_app"]
    best = next((s for s in web if s.reach == "explicit"), None) or next(iter(web), None)
    if best is None and app_url:
        best = web_surface(app_url, evidence=["Its address is known to Bevro"])
    return merge([best] if best else [], rest)


def has_a_way_to_use(surfaces: list[Surface]) -> bool:
    """Is there somewhere the person can go to use it, other than Bevro?"""
    return any(s.role == "use" for s in surfaces)


def sentence(s: Surface) -> str:
    """One plain sentence about one surface."""
    label = LABELS[s.kind]
    if s.kind == "web_app":
        return "It has its own web app."
    if s.kind == "schedule":
        if s.installed is False:
            return "It comes with a schedule that isn't set up on this machine."
        return f"It runs by itself, {s.when}." if s.when else "It runs by itself on a schedule."
    if s.kind == "command_line":
        return "It can be run from the command line on this machine."
    if s.role == "delivers":
        return f"It sends its results to {label}."
    return f"You can use it through {label}."


def public(s: Surface) -> dict[str, Any]:
    """What the browser may see: words, and a web address with how it was published."""
    out: dict[str, Any] = {"kind": s.kind, "role": s.role, "label": LABELS[s.kind], "sentence": sentence(s)}
    if s.kind == "web_app" and s.url:
        out["url"] = s.url
        out["reach"] = s.reach
        if s.title:
            out["title"] = s.title
    if s.kind == "schedule":
        out["when"] = s.when
        out["installed"] = s.installed
    return out
