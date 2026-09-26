# Automations

**Agents do work. Bevro owns when the work happens.**

A provider that can answer *"check the competitor landscape"* can be asked the
same thing every Monday — without being changed in any way. The recurrence,
the clock, the condition and the history belong to Bevro. No generated or
connected agent ever grows a loop of its own.

```
Automation → (due) → an ordinary Task → the usual ProviderRun and runtime
           → artifacts → for monitoring, a verdict → quiet, or surfaced
```

There is no second execution path. A scheduled run is created and driven by
the same task service a person's request uses, so permissions, credentials,
workspaces, availability, runtime fallback and approvals behave identically.
Scheduling never widens what a provider may do.

## The model

| | |
|---|---|
| **Automation** | title, instruction (the person's own words), mode, provider selection, schedule, condition, enabled, next/last run, and the fingerprint of the last result |
| **AutomationRun** | one occurrence: which Task it became, when it was due, when it began, whether it was delayed, why it ran, how it went, and — for monitoring — whether it matched and why |

Tasks stay the authoritative record of work; an occurrence only points at one.
Nothing duplicates artifacts.

**Modes.** `scheduled` — every run is the point. `monitoring` — it runs on the
same cadence but only speaks up when the condition is met.

**Provider selection.** `pinned` (set up from a task that worked: always that
agent) or `dynamic` (set up from a sentence: the router chooses each time,
exactly as it would for a person). A pinned agent is never silently replaced.

## Saying when

Nobody types cron. A sentence is parsed into a **ScheduleSpec**: recurrence,
time of day, weekdays or day of month, interval, timezone, start and end.

| What was said | What Bevro sets up |
|---|---|
| "every Monday morning" | Every Monday · 09:00 |
| "every day at 09:00" | Every day · 09:00 |
| "on the first day of every month" | The 1st of every month · 09:00 |
| "every hour" / "every 15 minutes" | Every hour / Every 15 minutes |
| "every other Monday" | Every 2 weeks on Monday · 09:00 |
| "every weekday morning" / "on weekdays" / "Mon-Fri" | Every weekday · 09:00 |
| "every weekend" | Every weekend day · 09:00 |
| "every evening" | Every day · 18:00 |

Words like *morning*, *noon*, *evening* and *night* have plain meanings (09:00,
12:00, 18:00, 21:00). The recurrence is also kept in one canonical line
(`FREQ=WEEKLY;INTERVAL=1;BYDAY=MO;…`) for Advanced details only — the person
never has to read it.

**Bevro never creates recurring work silently.** A recurring sentence on Home
is shown back first:

```
research new competitors
Every Friday · 09:00
[ Schedule ]  [ Just once ]
```

## Timezones, daylight saving and restarts

A schedule stores its **IANA timezone** explicitly; the machine's own timezone
is never assumed. The hour the person meant is the hour they get: a 09:00 job
in `Europe/London` stays at 09:00 local through the spring change, even though
its UTC hour moves. A wall-clock time that does not exist on the day the
clocks go forward runs at the first moment after it.

**Missed runs.** If Bevro was away — a restart, a machine asleep — the work
happens **once** when it comes back, not once for every interval that passed.
That occurrence is recorded as *delayed* with the trigger `catch_up`, and the
next turn is the next real one.

## The scheduler

A small process (`python -m app.scheduler`, its own Compose service) whose
only job is the clock. Each turn it:

1. claims what is due — `FOR UPDATE SKIP LOCKED`, writing the next turn
   immediately, with a unique constraint on (automation, occurrence) so two
   schedulers can never both create the same run;
2. creates an ordinary Task, routed or pinned;
3. drives quick providers itself and leaves long ones to the worker, exactly
   as the API does;
4. settles occurrences whose task has finished, and for monitoring decides
   whether the result is worth surfacing.

It keeps no state of its own: everything is a row, so it can be stopped,
restarted or run several times over. No Celery, no Redis — PostgreSQL is
already there.

## Monitoring

Recurring work with a condition, evaluated by a provider-neutral
**ConditionEvaluator**:

| Kind | Read from | Decides by |
|---|---|---|
| `changed` | "tell me when it changes" | comparing a fingerprint of the result; dates and spacing are not a change |
| `keyword` | "tell me when it mentions “free plan”" | the quoted words appearing |
| `threshold` | "tell me if the value exceeds 100" | the numbers in the result |
| `model` | anything else: "when there is a meaningful new competitor" | a small structured judgement |

The model sees three things: what the person asked to hear about, this
result, and the previous one. It is told that results are data, never
instructions, and it has no filesystem, network or provider access. Without a
model configured, a `model` condition falls back to comparing results — honest
and offline.

The first run of a monitoring automation is the starting point and stays
quiet. After that, a match raises a **notification**: unread in Bevro, and by
email or to a web address if this installation is set up for it and the
automation asks. A quiet run raises nothing at all and sends nothing.

Ordinary scheduled work is different on purpose: its results go to Recent, and
nobody is told unless the automation was asked to say when it finishes.
`notify()` in `app/services/automations.py` is still the single place an
occurrence is announced; how it reaches the person is
[docs/NOTIFICATIONS.md](NOTIFICATIONS.md).

## What the person sees

**Scheduled** (its own small navigation entry) is a list, not a dashboard:

```
Research competitor changes
Every Monday · 09:00 · Research
Ran yesterday · Next: Monday
Pause · Run now · Edit · Remove

Competitor watch
Every day · 09:00 · Notify when the result changes
No change last run · Next: today, 09:00 · Also by email
```

**Edit** also holds one small section, *When this needs my attention*: in
Bevro (always), email (only when this installation has it), and for scheduled
work, *tell me each time it finishes*.

A finished task offers **Run again** and **Schedule**, which asks one question
— *"When should Bevro run this?"* — with a checkbox for *"Only tell me when
something changes"*. **Run now** creates an ordinary task at once and leaves
the recurrence alone. **Pause** stops future turns and keeps all history;
**Resume** works out the next turn from now. Editing changes what is asked or
when, and never touches past tasks.

## Created and connected agents alike

If an AgentSpec mentions recurring work, the schedule is **not** built into the
generated project: the agent does the work, Bevro offers to set up when. The
same feature works identically for a connected HTTP provider, an MCP agent, a
CLI agent, a bridge-backed provider and a built-in one — if it can be invoked,
it can be scheduled.

## What is kept for Details

Each occurrence records the automation it belongs to, when it was due, when it
actually started, whether it was delayed, why it ran (`schedule`, `catch_up`,
`run_now`), the outcome, and for monitoring whether it matched and why. The
ordinary interface shows one plain line; the rest is there when needed.
