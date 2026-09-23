# Notifications

**Tasks record the work. Notifications tell the person when the result matters.**

Bevro decides when work happens. Providers do the work. Delivery decides how
you hear about a result that is worth hearing about. None of those three know
much about each other, and that is the point: no provider and no automation
contains any code about email, and adding a new way of being reached changes
nothing else.

```
an occurrence that mattered → NotificationEvent → one delivery per channel
                                    │
                            in Bevro (always) ─ email ─ webhook
```

---

## What raises an event

| Automation | What happens | Notification |
|---|---|---|
| Monitoring, condition not met | quiet run, recorded | none |
| Monitoring, condition met | surfaced run | **yes** |
| Scheduled, finished | result in Recent | none, unless asked |
| Scheduled, finished, "tell me each time" | result in Recent | **yes** |
| Scheduled, failed, "tell me each time" | failure in Recent | **yes** |

Those two meanings stay distinct on purpose. Monitoring is *tell me when
something is worth telling*; scheduled work is *do this again*, and its
results belong in Recent whether or not anyone is told.

Nothing else creates notifications. A task a person started themselves does
not: they are already looking at it.

---

## In Bevro first

Bevro is a proper destination, not a fallback. With nothing configured at
all — a fresh clone, no mail server, no accounts — notifications work:

* a dot on **Notifications** in the sidebar when something is unread;
* a compact list: what happened, why, how long ago, and a link to the result;
* **Needs your attention** on Home when unread events are waiting.

It is deliberately not an activity feed. Bevro only speaks when it has
something to say, and it never lists the work it did quietly.

### Read and unread

Opening the result marks that notification read. So does **Mark read**.
Opening the *list* marks nothing: looking is not reading. **Mark all read**
exists and is always an explicit choice.

---

## Channels

A channel answers three questions and nothing else:

```python
validate_configuration(where=None)   # usable here? if not, why, in words
health()                             # usable right now?
deliver(payload, where)              # send this; say plainly how it went
```

| Channel | What it needs | Notes |
|---|---|---|
| `in_app` | nothing | always available; the event row is the delivery |
| `email` | an SMTP server, and an address to send to | the first external channel |
| `webhook` | nothing but an address | small, stable, optionally signed JSON |

A channel whose machinery is missing is **not offered** in the automation
editor, so nobody can choose something this installation cannot do: without a
mail server, Email does not appear at all. If one is configured and later
removed, an automation that still asks for it records a plain "no mail server
is set up here" against that notification rather than pretending it was sent.

### Setting up email

Ordinary mail-server settings, nothing Bevro-specific. In `.env`:

```
BEVRO_SMTP_HOST=smtp.example.com
BEVRO_SMTP_PORT=587
BEVRO_SMTP_USERNAME=bevro@example.com
BEVRO_SMTP_PASSWORD=…
BEVRO_SMTP_FROM=bevro@example.com
BEVRO_NOTIFY_EMAIL=you@example.com      # who Bevro writes to
BEVRO_APP_URL=http://localhost:6140     # so the email can link back
```

Port 465 uses TLS from the start; anything else tries STARTTLS and carries on
without it if the server does not offer it (which is what a local test server
such as `python -m aiosmtpd -n -l localhost:1025` does).

None of this is required. Bevro starts, runs and notifies without it.

### Setting up a webhook

```
BEVRO_NOTIFY_WEBHOOK_URL=https://hooks.example.com/bevro
BEVRO_NOTIFY_WEBHOOK_SECRET=            # optional, but do set it
```

Bevro posts exactly this, and will keep posting exactly this:

```json
{
  "event": "automation.matched",
  "title": "Competitor watch",
  "summary": "Five competitors, including Acme.",
  "reason": "The result is different from last time.",
  "task_id": "…",
  "automation_id": "…",
  "created_at": "2026-09-22T08:00:00+00:00",
  "url": "http://localhost:6140/tasks/…"
}
```

Each request also carries:

```
Content-Type:       application/json
X-Bevro-Event:      automation.matched
X-Bevro-Timestamp:  1774000000          (only when signing is on)
X-Bevro-Signature:  sha256=<hex>        (only when signing is on)
```

### Checking the signature

With `BEVRO_NOTIFY_WEBHOOK_SECRET` set, the signature is an HMAC-SHA256 over
the exact bytes `<timestamp>.<body>`. The timestamp is signed as well as sent,
so yesterday's message cannot be replayed in today's clothes.

```python
import hashlib, hmac, time

def valid(secret: str, headers, raw_body: bytes) -> bool:
    timestamp = headers["X-Bevro-Timestamp"]
    if abs(time.time() - int(timestamp)) > 300:      # five minutes
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, headers.get("X-Bevro-Signature", ""))
```

Compare against the **raw body**, before any JSON parsing and re-encoding —
Bevro signs the bytes it sends. The secret never leaves the server and is
never returned to the browser.

### Somewhere of its own

The settings above are where this installation sends. One automation may
override them under **When this needs my attention**: an email address, or a
web address. Leave the field empty and the installation default is used.

That is the whole of it. There is no address book, no per-recipient
preferences and no groups — if you need those, a webhook into something that
does have them is the right shape.

An installation with a mail server but no `BEVRO_NOTIFY_EMAIL` can still email
a single automation that names its own address; Bevro then asks for one when
the channel is ticked. A webhook needs no installation setup at all: an
address is the only thing it requires.

---

## What never leaves Bevro

Payloads carry a short summary and a link back. Before anything is sent it is
scrubbed of filesystem paths (`/srv/…`, `~/agents/…`, `C:\…`), anything
shaped like a key or token, and `NAME=value` pairs whose name looks like a
credential. Web addresses survive: a URL is not a path. Long output is cut
short — the full result stays in Bevro, behind the link.

Never sent: provider secrets, `.env` contents, raw logs, raw model responses,
worker metadata, or whole artifacts. Where a message was sent is not returned
to the browser either: the browser learns *that* it went by email, never to
which address.

---

## Delivery never touches the work

A task that succeeded stays succeeded. Deliveries live in their own rows:

```
Task: completed          ← the work
Notification: raised     ← it mattered
  in_app:  sent
  email:   delivery_failed
```

Bevro **never re-runs a provider because a message failed to send.** A failed
delivery is retried on its own: after 1 minute, 5 minutes, then 30 minutes,
and then it stops and says so, with a **Try sending again** in the list.

A permanent problem is not retried at all. A wrong password, a rejected
address or a web address answering "no such thing" will be just as wrong in
half an hour, so Bevro records it and stops. A busy or unreachable server —
a timeout, a 4xx greeting, a 5xx answer — is temporary, and is tried again.

---

## One event, one message

Two guarantees, both enforced by the database rather than by careful code:

* `notification_events.automation_run_id` is **unique**, so one occurrence
  produces one event however many schedulers are running.
* a delivery is **claimed** (`FOR UPDATE SKIP LOCKED`, status moved to
  `sending`) before it is attempted, so two senders cannot both send it.

---

## Preferences

Small on purpose. Globally: in-app is on, email and webhook are whatever the
server is set up for. Per automation, in the editor under **When this needs
my attention**:

```
[x] In Bevro
[ ] Email                            (only shown when a mail server is set up)
    └ you@example.com                (optional: somewhere else)
[ ] Webhook
    └ https://example.com/hook       (optional: somewhere else)
[ ] Tell me each time it finishes    (scheduled work only)
```

The address field appears only once a channel is ticked. There is no
preference centre, no per-channel quiet hours and no digests.

---

## Where the code is

| What | Where |
|---|---|
| The event and its deliveries | `api/app/models/notification.py` |
| Deciding there is something to say | `api/app/services/notifications.py` |
| The channel contract | `api/app/delivery/base.py` |
| What leaves Bevro, and the scrubbing | `api/app/delivery/payload.py` |
| Email, webhook, in-app | `api/app/delivery/{email,webhook,in_app}.py` |
| Signing, and the recipe for checking it | `api/app/delivery/webhook.py` (`sign`, `verify`) |
| Sending, every turn | `api/app/scheduler.py` |
| The browser's view | `api/app/routers/notifications.py`, `web/src/pages/Notifications.tsx` |

Adding a channel means writing one class and listing it in
`api/app/delivery/__init__.py`. Nothing about automations, tasks, providers
or the browser has to change.
