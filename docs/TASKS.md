# Tasks: from "start this" to a result

Once routing has chosen an app or agent that Bevro can send work to
([ROUTING.md](ROUTING.md)), the person has one thing to follow: the **task**.
It says whether the work started, what is happening, whether it finished,
what it produced and, if it didn't, what they can do next. Nothing on the
way there needs them to know how Bevro reached the app.

A request Bevro only points somewhere ("Zekor is the best place for this")
or can't send yet ("needs a credential") never becomes a task.

## Task, run, attempt

| | What it is | Who sees it |
|---|---|---|
| **Task** | The person's request and its outcome: the goal, the app chosen, the status, the result. | Everyone. It is the one thing in Recent. |
| **Run** | One go at the work. "Try again" adds a run to the same task; the task stays one row in Recent. | Details, as "Attempt 1", "Attempt 2". |
| **Attempt** | One way of reaching the app, within a run. When a way in fails for reasons of its own (not the work's), the next is tried ([RUNTIMES.md](RUNTIMES.md)). | Details, as "Over the network: Couldn't reach it". |

## What the person sees

One status per task, worked out in one place (`task_status()` in
`api/app/schemas/serialise.py`) and used by every page. The database states
are unchanged; this is how they are told.

| Status | Sentence | Comes from |
|---|---|---|
| **Starting** | "Starting…" | a task whose run hasn't begun. Waiting for the helper on this computer is said as such. |
| **Working** | "Jobs Desk is working on this." | a run that has begun |
| | "Trying another way to reach Jobs Desk…" | a way in failed and the next is being tried. Not an error. |
| **Needs you** | "Jobs Desk needs an answer from you." | a question or approval is waiting |
| **Completed** | "Jobs Desk finished this." | the work finished |
| **Couldn't complete** | the failure's own sentence (below) | every way in and every recovery is exhausted |
| **Stopped** | "Stopped before it finished." | the person cancelled it |

Under the sentence, at most one more true thing: what the app says it is
doing (only if it reports that; nothing is invented, and there are no
percentages), that it worked after Bevro tried another way, that part of
the result couldn't be saved, or that nothing has been heard for a while.

The task page asks the server again by itself - often at first, less as
work goes on - until the task is finished, needs the person, or stopped.
Recent refreshes itself while anything in it is still going. Only the status
sentence is a live region, so a screen reader hears when the task moves on,
not every poll.

## Failures

Each says what happened and to whom, never blaming the app for Bevro's own
problem:

| Category | Sentence | Next steps | Try again may repeat work? |
|---|---|---|---|
| `credential_required` | "... needs a credential before it can run." | Add credential, Try again | no |
| `provider_unavailable` | "I couldn't reach ..." | Try again, Open app, Check ... | no |
| `configuration_problem`, `not_started` | "I couldn't start this with ..." | Go to ..., Try again | no |
| `invocation_failed` | "... started the work but couldn't complete it." | Try again, Go to ... | yes |
| `timed_out` | "... took too long and was stopped." | Try again, Go to ... | yes |
| `output_invalid` | "... answered, but Bevro couldn't read the result." | Try again, Go to ... | yes |
| `lost_contact` | "Bevro lost contact with ... while it was working on this." | Try again, Open app, Go to ... | yes |
| `bevro_error` | "Bevro hit a problem while handling this." | Try again, Go to ... | if it had started |

While Bevro cannot reach the app at all, "Try again" is replaced by
"Check ..." (which looks again) and "Open app"; it comes back once the app
can be reached.

## Trying again

Try again is the same task with a new run, never a new task. It is only
possible from Couldn't complete, so a second press, or a refresh repeating
it, is refused rather than starting a second run.

When the app may already have acted on the request (the "may repeat" column
above), the page asks first: "Try again may send this request to ...
again." When Bevro knows the request never reached the app, it just tries.
Run again on a completed task is a deliberate new task.

A task whose app has since been removed stays readable under the name it
had, and cannot be tried again.

## Results

Artifacts are shown result first: one the app marks as its main result
(`metadata.primary`), otherwise something to read (a report, note or text)
before a table or file, before a link into another app. The rest are under
"More results". Files open and download through Bevro
(`/api/artifacts/{id}/content`); where they are stored is never shown, and
anything in an artifact's metadata that looks like a place on a disk is
dropped before it reaches the browser. A wide table scrolls within its own
box. A result that couldn't be stored is said, not silently lost.

## Cancelling

Offered only where Bevro can really stop the work:

- before the run has begun, or while a question is waiting: always;
- work on the helper on this computer, when that way of running it can be
  stopped (a program Bevro started is ended);
- **not** once a request has been sent to an app over the network: nothing
  Bevro does takes it back, so there is no Cancel button, and asking the API
  to cancel is refused with that sentence.

## Quiet and stale work

Every process that runs work (the API, the scheduler, the helper) sends a
heartbeat for it every few seconds while it works.

- Nothing heard for 45 seconds: "Bevro hasn't had an update recently", with
  Check again and a way to the app. Not a failure; a slow app is not a
  broken one.
- Nothing heard for `BEVRO_WORKER_STALE_SECONDS` (90 s): the process
  running it has stopped, so the task ends as **lost contact**.
- Work the API or scheduler was going to start and never did, after the
  same time: **couldn't start** (safe to try again).
- Work waiting for the helper on this computer is not stale. It is waiting,
  and says so.

The scheduler checks for both on every tick, so this works with or without
the helper.

## Scheduled work

Each occurrence of an automation is an ordinary task and run
([AUTOMATIONS.md](AUTOMATIONS.md)), so it is told exactly the same way.

## For providers

Nothing is required. A provider that wants to say more can: report phases
through `context.progress("Reviewing opportunities")`, and mark its main
result with `metadata: {"primary": true}`. Bevro adapts to providers, not
the other way round.

## Privacy

What the browser gets for a task is names and plain sentences. The status,
failure, ways tried (by the display name Bevro gave each, never an address
or command) and results. Never secrets, environment, command lines,
workspace or storage paths, raw provider errors or run metadata. A task's
Details show what the app itself said about a failure, and a reference for
support.
