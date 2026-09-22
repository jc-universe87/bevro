# Security

## Reporting a vulnerability

Please do not open a public issue for anything that could be exploited.
Use GitHub's private vulnerability reporting on this repository ("Security"
→ "Report a vulnerability"). Include what you found, how to reproduce it and
what you think the impact is. You will get an acknowledgement, and a fix or
an explanation.

Things that are fine as public issues: hardening suggestions, questions about
the boundaries below, documentation gaps.

## What Bevro is, security-wise

Bevro is a local-first workspace that hands tasks to providers. In its
default installation it has **no authentication and no multi-user model**:
it is meant to run on your own machine or a trusted network. Do not expose
the API or web ports to the internet as they are.

## Boundaries

**Provider credentials.** Secrets entered under Connect are encrypted at
rest (Fernet) with `BEVRO_SECRET_KEY`, or a key generated once into
`data/secret.key` when none is set. They are decrypted only inside the API or
worker at invocation time and never returned by any endpoint. Protect the
key file and the database as you would the credentials themselves.

**External integrations.** An HTTP or MCP provider you connect receives the
text of the tasks routed to it. Connect only services you trust with that
text.

**Routing model.** With `BEVRO_ROUTER_MODE=llm`, every request typed on
Home is sent to the configured model provider together with a sanitised
list of your agents (names, descriptions, capabilities, availability). No
secrets, paths, endpoints or history go with it, and the model's answer is
only a proposal that Bevro validates; it cannot run anything. The default
mode, `deterministic`, sends nothing anywhere.

**Coding providers (Claude Code and any future one).** This is the sharp
end, and it is important to be precise about it:

- A coding provider runs as the user who starts `scripts/worker.sh`, on that
  machine, inside a directory listed in `config/workspaces.json`. Bevro
  never lets a prompt or the browser choose a path.
- With `write` permission it modifies files in that directory. With
  `run_commands` permission it runs shell commands. **A shell command can in
  principle read or affect anything that user can.** Bevro passes a
  deny-list (`git push`, `sudo`, `rm -rf /`, `git reset --hard`, …) and
  Claude Code's `--restricted` mode, which confines file tools to the
  working directory and denies anything that would prompt a human. That is
  a set of guard rails, **not a sandbox**. Bevro does not claim guarantees
  that the underlying tool does not provide.
- Grant `run_commands` only to directories where that is acceptable.
  `read` + `write` is enough for many tasks. Review "View changes" before
  trusting a result. Never point a workspace at your home directory.
- Raw tool output, subprocess arguments and logs stay in `data/logs/` on the
  server and are never served to the browser.

**What the browser can see.** API responses are allow-listed: no filesystem
paths, no adapter configuration, no secrets, no worker metadata, no raw
provider output. Tests in `api/tests/` assert this; if you find a leak,
that is a vulnerability — report it.

## Supported versions

Pre-1.0: only the current `main` branch receives fixes.
