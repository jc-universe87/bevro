# Coding providers: Claude Code in Bevro

Claude Code is the first real external provider. It is not special in the
domain model: it is a `Provider` row whose adapter kind is `claude_code`, its
work is `ProviderRun`s on ordinary `Task`s, and its output is ordinary
`Artifact`s. Everything Claude-specific lives in `adapters/claude_code.py`
and in `ProviderRun.meta`.

```
UI → API → Task + ProviderRun (pending, execution = "background")
                      ↓ PostgreSQL (FOR UPDATE SKIP LOCKED)
              worker process on the host  (scripts/worker.sh)
                      ↓ ClaudeCodeAdapter.invoke()
              `claude -p … --output-format stream-json`  in the approved workspace
                      ↓ progress, heartbeats, cancel checks → PostgreSQL
              InvocationResult → summary + artifacts on the run's task
```

## Execution architecture

**Why a worker.** A coding job can take minutes. Running it inside an HTTP
request is not an option, and the `claude` CLI, its login and the workspaces
all live on the host — so the worker runs on the host, not in Docker. It
shares only PostgreSQL and the `data/` directory with the API.

**How work is handed over.** `submit()` writes a `ProviderRun` with
`execution = "background"` and leaves it `pending`. The worker polls every
second with `SELECT … FOR UPDATE SKIP LOCKED`, so two workers never take the
same run. Taking a run marks it `running` (task `working`) with the worker's
id and a heartbeat.

**Adapters declare how they run.** `ProviderAdapter.execution` is `"inline"`
(Research, Event Allocation Demo, HTTP providers: the API runs them in a background thread
as before) or `"background"`. Adding Codex is adding another background
adapter; nothing else changes.

**Progress.** The adapter gets an `InvocationContext`. `context.progress("Running checks")`
is persisted to `ProviderRun.progress` (`phase` + a capped list of `steps`).
The phase comes from the *kind* of tool Claude is using, never from its
output: Read/Grep → "Inspecting the project", Edit/Write → "Making changes",
a test/build command → "Running checks".

**Heartbeats and orphans.** The worker beats every 5 s. A `running`
background run with no heartbeat for `BEVRO_WORKER_STALE_SECONDS` (90) is
failed with "The task was stopped before completion." by the next worker
that looks — so a killed worker never leaves a task spinning.

**If the API container restarts:** nothing happens to the run. The worker
owns the process; the API only reads state. This was tested: a run kept
going through `docker compose restart api` and could still be cancelled
afterwards.

**If the worker stops:** the CLI process is stopped with it (the worker
cancels anything in flight on SIGTERM/SIGINT); the run is failed honestly
either by the worker on the way out or by the stale-run check later. Runs
still `pending` simply wait until a worker is back. Nothing is lost, but
nothing is retried automatically.

**If no worker is running at all:** the provider reports as unavailable
(the worker's last report expires after 150 s), Agents shows "Not available
on this installation" without an Ask action, and a coding request is
declined at once with "Coding help isn't set up on this installation yet."
rather than queued into the void.

## Workspace registry

Claude Code is never given a path from a prompt or a browser.

- `config/workspaces.json` (untracked; start from `workspaces.example.json`)
  is the only source of workspaces. The API seeds the `workspaces` table from
  it on start (`BEVRO_WORKSPACES_FILE`). Without the file there are no
  workspaces and a coding task fails with "No project is set up for this kind
  of work yet."
- The browser sees `id`, `name`, `description` and a permission summary —
  never the path (`GET /api/workspaces`, the task's `runs[].workspace`).
- A run stores only `workspace_id`. The path is resolved server-side at
  execution time (`tasks.build_request`) and validated again by the adapter
  (`adapters/workspace.py: validate_workspace_path`: absolute, exists, is a
  directory, `~` expanded).
- `resolve_workspace()` accepts nothing containing `/` or `\`, nothing that
  is not a UUID, and nothing disabled. Tests cover `/etc`, `../../`, slugs,
  names and raw paths.

**Choosing a workspace.** If the request names exactly one workspace
("…in Example project"), it is used. Otherwise the task goes to `needs_input`
with one question — *Which project should I work on?* — and the enabled
workspaces as options. `POST /api/tasks/{id}/input {"value": "<workspace id>"}`
answers it; anything not in the options is rejected (422). This is
progressive disclosure; Home has no project selector.

## Permissions

A workspace declares the most a run may do: `read`, `write`, `run_commands`.
The run copies them into `input.permissions` when the workspace is granted,
so the record of what it was allowed to do is on the run itself. The UI shows
them as plain sentences: *Claude Code can: Read and modify files in Bevro ·
Run project commands*.

They map to CLI flags (`build_command`):

| Bevro permission | Claude Code |
|---|---|
| read (always) | `--restricted --tools Read,Glob,Grep --permission-prompts none` |
| write | adds `Edit,Write,MultiEdit,NotebookEdit` and `--permission-mode acceptEdits` |
| run_commands | adds `Bash` and `--allowedTools Bash` |

Every run also gets `--no-session-persistence`, `--max-turns`,
`--max-budget-usd` (defaults 40 and $2.00, per provider config) and a system
prompt asking for a final one-line *Summary:*.

## Approval boundaries

There is no interactive approval in this phase. Instead, operations that
must never happen silently are blocked outright with `--disallowedTools`:
`git push`, `sudo`/`su`/`doas`, `rm -rf /`/`~`/`.`, `git reset --hard`,
`git clean`, `git checkout -- .`, `dropdb`, `psql … DROP`,
`docker compose down -v`, `chmod -R`, `chown`, `mkfs`, `dd`. With
`--permission-prompts none`, anything that would have asked a human is
denied. `--restricted` confines the file tools to the working directory and
ignores personal Claude settings, so a run cannot inherit a broader
allow-list from `~/.claude`.

What this does **not** guarantee: `run_commands` means Bash. A shell command
can in principle reach outside the workspace, and a pattern deny-list is a
deny-list, not a sandbox. Bevro reports what Claude Code did; it does not
claim Claude Code cannot do things Claude Code itself does not prevent. Give
`run_commands` only to workspaces where that is acceptable, or leave it out
(read + write is enough for many tasks).

## Process lifecycle and cancellation

The CLI is started with `start_new_session=True` (its own process group),
stdout piped, stderr and the raw stream-json written to
`BEVRO_LOG_DIR/<run id>.jsonl` / `.stderr`. Those logs are server-side only.

**Cancel** (`POST /api/tasks/{id}/cancel`): the task becomes `cancelled`
immediately; a running background run gets `cancel_requested`. The worker
polls that flag about once a second, sends SIGTERM to the process group,
SIGKILL after 5 s, then records the run as `cancelled` with whatever files
had already changed. Tested with the fake CLI (unit) and the real CLI
(manually): the `claude -p` process is gone afterwards.

A run that exceeds `timeout_seconds` (1800) is stopped the same way and
recorded as failed, not cancelled.

## Artifact generation

After the CLI exits, the adapter compares the workspace with its state before
the run:

- **Git repository:** `git status --porcelain` before and after, plus content
  hashes of files that were already dirty. A file dirty before *and* after
  counts as changed only if its content changed during the run. Files that
  were dirty before and untouched are listed as `pre_existing` and named in
  the artifact summary ("1 file was already modified before this task").
  Branch, HEAD and dirty state go into `ProviderRun.meta.git_before/after`.
- **No git:** a snapshot of `(size, mtime)` per file, ignoring
  `node_modules`, `.venv`, `__pycache__`, `dist` and similar.

Artifacts produced:

| Type | Title | Content |
|---|---|---|
| `report` | What Claude Code did | Claude's final message (Markdown), sanitised, minus the *Summary:* line |
| `structured` | Changed files | rows of `[path, Created/Modified/Deleted]`; metadata carries the workspace id and the pre-existing list |
| `diff` | View changes | `git diff` of what the run touched, new files included, capped at 200 KB (git workspaces only) |
| `deep_link` | Open <workspace> | only if `repository.remote_url` is configured |

The task summary is the *Summary:* line Claude was asked for, or
"Done. N files changed." if it forgot. Nothing is copied out of the
workspace; the changed files stay where they are.

Sanitisation (`strip_noise`) removes ANSI escapes and control characters and
caps length. Failures are always one of a small set of sentences ("Claude
Code could not start.", "The workspace is unavailable.", "The task was
stopped before completion.", "Claude Code could not complete this request.",
"…reached its step limit."). Stack traces and CLI dumps stay in the log file.

## Git awareness

Captured before and after: branch, HEAD, dirty files. Claude is told not to
commit or push, `git push` is blocked, and Bevro never commits. Pre-existing
work in a dirty workspace is preserved and attributed correctly (tested with
`dirty.txt` in `api/tests/test_coding.py`; confirmed with `NOTES.md` in the
real run against a throwaway project).

## Security boundaries, in one list

1. Paths come only from `config/workspaces.json`; ids are the only handle the
   browser or a prompt can use.
2. The browser never receives paths, `ProviderRun.input`, `meta`, worker ids
   or logs (`RunOut` is an allow-list; tests assert it).
3. Permissions are per workspace and copied onto the run.
4. Dangerous commands are denied; prompts to a human are impossible and thus
   denied; file tools are confined to the working directory.
5. The CLI runs with the worker's user, with `CLAUDECODE`/`CLAUDE_*`
   environment stripped, using the machine's existing Claude Code login. No
   credentials are copied into Bevro.
6. Budget and turn caps per run.

## How Codex (or any other coding agent) plugs in

1. Write `adapters/codex.py` with `kind = "codex"`, `execution = "background"`,
   `requires = frozenset({"workspace"})`, implementing `check`, `invoke`,
   `cancel`. Reuse `adapters/workspace.py` for validation, git state,
   change detection and diffs — it is provider-neutral.
2. Register it (`register_adapter(CodexAdapter())`, import it in
   `adapters/registry.py`).
3. Add `providers/manifests/codex.json`.

The worker picks up `codex` runs automatically (it handles every
`background` kind). Task, ProviderRun, Artifact, the workspace registry, the
question flow, permissions display and the UI need no change.

## Enabling Claude Code on your machine

Claude Code is optional. A fresh Bevro installation doesn't list it: Apps &
agents holds only what you connected, created or chose to add. Bevro offers
it on Connect (and on Create when nothing can build) as **Add Claude Code**;
until then coding requests are declined with a plain sentence. To enable it:

1. **Install and sign in to Claude Code** on the machine that will run the
   worker (see Anthropic's Claude Code documentation). Check with
   `claude --version`. Bevro uses that login; it never copies credentials.
2. **List the directories it may work in.** Copy
   `config/workspaces.example.json` to `config/workspaces.json` and edit the
   entries: a name, an absolute path *on this machine*, and permissions
   (`read`, `write`, `run_commands`). This file is ignored by git.
   Restart the API afterwards (`docker compose restart api`) so it re-reads
   the file.
3. **Run the worker** next to the Docker stack:

   ```sh
   python3 -m venv .venv && .venv/bin/pip install -r api/requirements.txt   # once
   ./scripts/worker.sh                                                      # keep it running; Ctrl-C stops it
   ```

   The script finds the project root itself and reads `.env` if present.
   Add it under Connect if you haven't. Within a minute it shows **Use in
   Bevro** under Apps & agents. Stop the worker and it says it's waiting for
   this computer.

The worker reports one of four states, shown as a short note: *available*,
*not installed* (no `claude` on the worker's PATH), *not signed in* (no
Claude Code login found — a heuristic: a credentials file under
`~/.claude` or an `ANTHROPIC_API_KEY`), or *unavailable* (no worker
reporting, or the CLI would not start). Technical detail stays in the worker
log.

## Development workflow

- Try things in a **throwaway project first**: a small git repository with a
  test or two. Leave one file deliberately modified but uncommitted and you
  can see pre-existing work being left alone and reported as such.
- If you list **the Bevro checkout itself** as a workspace, be aware: the
  API bind-mounts `api/`, `adapters/`, `providers/` and hot-reloads on save,
  and the web dev server hot-reloads `web/`. A coding run that edits those
  files changes the running development stack while it works. Do not run a
  coding task on Bevro while you are rebuilding or migrating the stack, and
  review `View changes` before trusting the result. The worker's own files
  (`data/`) are excluded from change detection.
- Automated tests never touch the real CLI: `api/tests/fake_claude.py` stands
  in for it. The one real-CLI test is opt-in (see DEVELOPMENT.md).
