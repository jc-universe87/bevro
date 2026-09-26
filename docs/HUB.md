# Apps & agents: what Bevro knows, how you use it, what Bevro can send work to

Bevro is a hub for the apps and agents a person builds and uses. Something
belongs in it because the person uses it, not because Bevro can drive it.
Three questions are kept apart, and never collapsed into one "connected"
flag:

| | Question | Where the answer lives |
|---|---|---|
| **Known** | Does Bevro know this thing, and what it is for? | a `providers` row: name, description, capabilities, `source` |
| **Ways to use it** | How does the person use it, and where do its results go? | `providers.surfaces` (evidence), turned into words when shown |
| **Direct** | Can Bevro itself send it work, right now? | derived from `runtimes`, as before (`reconcile.state_of`) |

A web app with no programmatic interface is therefore a complete, healthy
item: known, used through its own app, and not directly usable by Bevro.
Nothing about it is shown as a fault.

## Why this shape

Before this change `confirm_draft` refused anything without an invocable
runtime, so "connected" meant "callable". The provider table never required
that: Create already stores providers with nothing to run (`declared`), and
`state_of` already derives "nothing usable" rather than storing it. So the
smallest coherent extension is:

- **Connect may add a known item with no way in for Bevro**, as long as it has
  a way for the person to use it (or the person says what it is for). Its
  `adapter` is empty, its runtimes are whatever was found (none invocable), and
  nothing fake is invented to make it look callable.
- **One new column**, `providers.surfaces`: the ways of use Bevro found, as
  evidence (kind, how sure, why), refreshed whenever discovery looks again.
  Persisting evidence and deriving words follows the rule in
  `services/reconcile.py`.
- **Direct access is derived**, never stored: `ready`, `needs_credential`,
  `waiting_for_worker`, `needs_start`, `unreachable`, `paused`, or
  `not_set_up`. Only the first six describe a way in that exists; `not_set_up`
  means there never was one, which is neutral, not a problem.

`app_url` keeps its meaning: an address the person gave, or one the provider
declares for itself. A website Bevro merely *found* running is a surface.

## Surfaces

A surface is one way the person meets the thing. Each has a `kind`, a
`role` and the evidence it came from:

| kind | role | Evidence (two independent signals where one would be ambiguous) |
|---|---|---|
| `web_app` | use | a website answering on this machine, a declared `app_url`, a private-network share of that port |
| `telegram`, `slack`, `discord` | use (a bot that takes messages) or `delivers` (sends results there) | the platform's SDK or Bot API, **and** its token variable or a README mention. Receiving updates (`getUpdates`, webhooks, handler frameworks) makes it a bot; only sending makes it a destination |
| `schedule` | runs | a systemd timer shipped with the project, with whether it is installed and when it fires |
| `command_line` | use | a command-line entry point Bevro found |

`role` is what keeps "Available through Telegram" from being said of
something that only posts reports there.

Bevro itself is not stored as a surface: when direct access is `ready` (or
only lacks a credential) the interface says "Bevro" first.

## Opening a web app from a browser

Where Bevro can reach a service and where a person's browser can are
different questions. A service on `127.0.0.1` answers the worker and fails
on a phone.

So a web surface carries the address **and how it was published**:

| `reach` | Meaning | What the browser gets |
|---|---|---|
| `explicit` | the person gave it, or the provider declares it | the address as it is |
| `shared` | found in a private-network share of the port on this machine (`tailscale serve`) | the shared address |
| `network` | a real network address (LAN, VPN, public) | the address as it is |
| `all_interfaces` | published on every interface of this machine | the address, with the host the browser used for Bevro, when that host is this machine seen directly |
| `loopback` | published on this machine only | the address, only if the browser is on this machine too |

The browser works out the last two itself (`web/src/lib/hub.ts`), because
only it knows how it reached Bevro. "Seen directly" means an IP address, a
`localhost` name, or an explicit port: behind a reverse proxy on 443 the
host name belongs to the proxy and substituting it would produce a broken
link. When no address is safe, the page says where the app can be opened
instead of showing a button that fails, and offers to remember the address
the person uses.

## What the person sees

- **Apps & agents** lists everything known: the name, one plain sentence, how
  it is used ("Bevro · Web app", "Web app", "Runs on a schedule · sends to
  Telegram") and one primary action chosen by `hubActions()`:
  - Bevro can take work → **Use in Bevro**; Open app is secondary.
  - It can't, but it has a safe web address → **Open *Name***; direct access
    set-up is secondary and quiet.
  - The only thing missing is a credential → **Add credential**, and the
    sentence names it when Bevro can tell ("Direct use in Bevro needs an
    OpenAI API key"). That is said only when a missing credential blocks
    *every* way in that could take work: a way in that needs nothing from
    the person but is down is said as such instead, and a key Bevro holds for
    one item never counts for another. A key a schedule gets from elsewhere
    (a systemd credentials file, say) is that schedule's, not Bevro's.
  - Its API serves several profiles and nobody has said which →
    **Choose which one**.
  - A way in that used to work has stopped → **Try again** (a real problem).
  - Otherwise → **How to use it**.
- Every item is the person's to manage from the list itself: its name links
  to its page, and a quiet **…** in the row's corner offers **Details** and
  **Remove from Bevro**. Removing asks first, in the same words as on the
  item's page, and the item is gone at once; its task history stays.
- Warning styling is kept for real problems: a way in that stopped answering,
  a credential that is needed, the worker away.
- Home asks "What do you want to get done?" and answers with the app or
  agent for it, why, and the one thing to do next - starting work only when
  Bevro is sure and can do it itself (docs/ROUTING.md).

## Whose fact is this?

Everything Bevro records about an item was found for that item, from where
it lives, and is replaced - not added to - when Bevro looks again. Two
things keep that true when projects look alike:

- **A running service is asked where it listens.** The port a project's
  process listens on is probed at the address it is bound to; a service
  bound only to, say, a private-network address does not answer on
  127.0.0.1, and treating that silence as "not running" is how an API gets
  missed and a framework's default port guessed instead.
- **A name is not an identity.** Units and timers are found by the names of
  files a project ships; what systemd has installed under such a name counts
  for the project only if it runs from the project's folder. Otherwise its
  state, start command, credential files and schedule are someone else's.

What a running service declares it can do outranks words picked out of its
README, and what the person wrote about what an item is for outranks both -
but it never creates a way in, a schedule or a credential.

## Not done here

Surfaces are inferred from static evidence only; Bevro builds no Telegram,
Slack or email integration. Email is not inferred at all: sending mail is far
more often a notification than a way of use.
