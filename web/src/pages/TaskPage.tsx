import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ArtifactView from "../components/ArtifactView";
import { Page } from "../components/PageHeader";
import TaskStatus from "../components/TaskStatus";
import { api, ApiError, isTerminal, type TaskDetail } from "../lib/api";
import { fullDateTime } from "../lib/format";
import { REMOVE_TASK_DETAIL, REMOVE_TASK_QUESTION } from "./Recent";

/** Where a failure's actions lead. add_credential opens Manage for that provider with the field ready. */
function actionTarget(kind: string, providerId: string): string {
  if (kind === "add_credential") return `/agents?manage=${providerId}&credential=1`;
  if (kind === "test_connection") return `/agents?manage=${providerId}&test=1`;
  return `/agents?manage=${providerId}`;
}

// Quick work deserves a quick answer; a coding run that takes ten minutes does
// not deserve a request every 800ms for all of it. Watch closely at first,
// then ease off.
const POLL_STEPS = [
  { until: 20_000, every: 800 },
  { until: 120_000, every: 2_000 },
  { until: Infinity, every: 5_000 },
];

function pollDelay(watchingForMs: number): number {
  return (POLL_STEPS.find((s) => watchingForMs < s.until) ?? POLL_STEPS[POLL_STEPS.length - 1]).every;
}

export default function TaskPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answering, setAnswering] = useState(false);
  const [answered, setAnswered] = useState(false);
  const [answerText, setAnswerText] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [scheduling, setScheduling] = useState(false);
  const [when, setWhen] = useState("");
  const [onlyWhenChanged, setOnlyWhenChanged] = useState(false);
  const [scheduleNote, setScheduleNote] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const [busy, setBusy] = useState(false);

  const removeTask = async () => {
    if (!task) return;
    setBusy(true);
    try {
      await api.removeTask(task.id);
      navigate("/recent");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That couldn't be removed.");
      setBusy(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startedWatching = Date.now();
    const load = async () => {
      try {
        const t = await api.getTask(id);
        if (cancelled) return;
        setTask(t);
        if (!isTerminal(t.state)) timer = setTimeout(load, pollDelay(Date.now() - startedWatching));
      } catch {
        if (!cancelled) setError("This task couldn't be loaded.");
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [id, answered]);

  const cancel = async () => {
    if (!task) return;
    try {
      setTask(await api.cancelTask(task.id));
    } catch {
      /* the poll will pick up whatever state it is really in */
    }
  };

  const retry = async () => {
    if (!task || retrying) return;
    setRetrying(true);
    setRetryError(null);
    try {
      setTask(await api.retryTask(task.id));
      setAnswered((v) => !v); // restart polling
    } catch (err) {
      setRetryError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
    } finally {
      setRetrying(false);
    }
  };

  const runAgain = async () => {
    if (!task) return;
    setBusy(true);
    try {
      const again = await api.submitTask({ request: task.original_request, provider_id: task.provider?.id });
      navigate(`/tasks/${again.id}`);
    } catch (err) {
      setScheduleNote(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const schedule = async () => {
    if (!task) return;
    setBusy(true);
    setScheduleNote(null);
    try {
      const automation = await api.createAutomation({
        when: when.trim(),
        task_id: task.id,
        only_when: onlyWhenChanged ? "only tell me when the result changes" : undefined,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      });
      setScheduling(false);
      setScheduleNote(`Scheduled: ${automation.schedule}.`);
    } catch (err) {
      setScheduleNote(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const answer = async (value: string) => {
    if (!task || answering) return;
    setAnswering(true);
    try {
      setTask(await api.answerInput(task.id, value));
      setAnswered(true);
    } catch {
      /* leave the question on screen */
    } finally {
      setAnswering(false);
    }
  };

  if (error) {
    return (
      <Page narrow>
        <p role="alert">{error}</p>
        <Link to="/recent" className="bv-link text-sm">
          Back to Recent
        </Link>
      </Page>
    );
  }
  if (!task) {
    return (
      <Page narrow>
        <p className="bv-hint">Loading…</p>
      </Page>
    );
  }

  const active = !isTerminal(task.state);
  const failed = task.state === "failed";
  const waitingForYou = task.state === "needs_input" && task.input_request;
  const run = task.runs[task.runs.length - 1];
  const failure = failed ? run?.failure ?? null : null;
  const hasOutcome = !waitingForYou && !failure && (Boolean(task.summary) || task.artifacts.length > 0);
  const steps = run?.steps ?? [];
  const showSteps = active && !waitingForYou && steps.length > 0;

  return (
    <Page narrow>
      <nav className="text-sm text-muted mb-6" aria-label="Breadcrumb">
        <Link to="/recent" className="hover:text-ink">
          Recent
        </Link>
        <span aria-hidden="true"> / </span>
        <span className="text-ink">{task.title}</span>
      </nav>

      <section aria-label="Request" className="mb-8">
        <p className="text-lg md:text-xl leading-snug">{task.original_request}</p>
        <p className="bv-hint mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
          {task.provider && <span>{task.provider.name}</span>}
          <span aria-hidden="true">·</span>
          <TaskStatus state={task.state} />
          <span aria-hidden="true">·</span>
          <time dateTime={task.created_at}>{fullDateTime(task.created_at)}</time>
          {active && (
            <button type="button" onClick={cancel} className="bv-link ml-auto">
              Cancel
            </button>
          )}
        </p>
        {run?.workspace && run.permissions.length > 0 && (
          <p className="bv-hint mt-1 text-xs">
            {run.provider.name} can: {run.permissions.join(" · ")}
          </p>
        )}
      </section>

      {waitingForYou && task.input_request && (
        <section aria-label="Question" className="border-t border-line pt-6">
          <p className="text-base md:text-lg">{task.input_request.question}</p>
          {task.input_request.kind === "text" ? (
            <form
              className="mt-3 flex flex-col sm:flex-row gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (answerText.trim()) void answer(answerText.trim());
              }}
            >
              <label htmlFor="task-answer" className="sr-only">
                Your answer
              </label>
              <input id="task-answer" autoFocus value={answerText} onChange={(e) => setAnswerText(e.target.value)} className="bv-input" disabled={answering} />
              <button type="submit" className="bv-btn-primary" disabled={answering || !answerText.trim()}>
                Send
              </button>
            </form>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              {task.input_request.options.map((o) => (
                <button key={o.value} type="button" onClick={() => answer(o.value)} disabled={answering} className="bv-btn">
                  {o.label}
                </button>
              ))}
            </div>
          )}
        </section>
      )}

      {showSteps && (
        <section aria-label="Progress" className="border-t border-line pt-6">
          <ol className="space-y-1 text-sm">
            {steps.map((step, i) => (
              <li key={i} className={i === steps.length - 1 ? "text-ink" : "text-muted"}>
                {step}
              </li>
            ))}
          </ol>
        </section>
      )}

      {failure && run && (
        <section aria-label="What went wrong" className="border-t border-line pt-6">
          <h2 className="font-medium">{failure.title}</h2>
          <p className="mt-1 text-base md:text-lg text-muted">{failure.message}</p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {failure.actions.map((a, i) =>
              a.kind === "retry" ? (
                <button key={a.kind} type="button" onClick={retry} disabled={retrying} className={i === 0 ? "bv-btn-primary" : "bv-btn"}>
                  {retrying ? "Retrying…" : a.label}
                </button>
              ) : (
                <Link key={a.kind} to={actionTarget(a.kind, run.provider.id)} className={i === 0 ? "bv-btn-primary" : "bv-btn"}>
                  {a.label}
                </Link>
              ),
            )}
            {!failure.actions.some((a) => a.kind === "manage") && (
              <Link to={actionTarget("manage", run.provider.id)} className="bv-btn-quiet">
                Manage provider
              </Link>
            )}
            {retryError && (
              <p role="alert" className="basis-full text-sm">
                {retryError}
              </p>
            )}
          </div>
          {task.artifacts.length > 0 && (
            <ul className="mt-5 space-y-5">
              {task.artifacts.map((a) => (
                <li key={a.id}>
                  <ArtifactView artifact={a} />
                </li>
              ))}
            </ul>
          )}
          <details className="mt-5 text-sm">
            <summary className="cursor-pointer text-muted hover:text-ink">Details</summary>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-muted">
              <dt>Provider</dt>
              <dd>{run.provider.name}</dd>
              <dt>Failure</dt>
              <dd>{failure.title}</dd>
              <dt>Time</dt>
              <dd>{run.completed_at ? fullDateTime(run.completed_at) : "—"}</dd>
              <dt>Run ID</dt>
              <dd className="font-mono text-xs">{run.id}</dd>
              {task.runs.length > 1 && (
                <>
                  <dt>Attempts</dt>
                  <dd>{task.runs.length}</dd>
                </>
              )}
            </dl>
          </details>
        </section>
      )}

      {hasOutcome && (
        <section aria-label="Outcome" className="border-t border-line pt-6">
          {task.summary && <p className={`text-base md:text-lg ${failed ? "text-muted" : ""}`}>{task.summary}</p>}
          {run?.recovered && <p className="bv-hint mt-1">Recovered using another connection.</p>}
          {task.artifacts.length > 0 && (
            <ul className="mt-5 space-y-5">
              {task.artifacts.map((a) => (
                <li key={a.id}>
                  <ArtifactView artifact={a} />
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {!hasOutcome && active && !waitingForYou && !showSteps && (
        <p className="bv-hint border-t border-line pt-6">
          {task.state === "queued" ? "Waiting for an agent to pick this up." : "Bevro will show the result here as soon as it's ready."}
        </p>
      )}

      {task.state === "completed" && (
        <section aria-label="Next" className="mt-8 border-t border-line pt-6">
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" className="bv-btn" onClick={runAgain} disabled={busy}>
              Run again
            </button>
            {scheduling ? null : (
              <button type="button" className="bv-btn" onClick={() => setScheduling(true)}>
                Schedule
              </button>
            )}
          </div>
          {scheduling && (
            <form
              className="mt-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (when.trim()) void schedule();
              }}
            >
              <label htmlFor="task-when" className="bv-label">
                When should Bevro run this?
              </label>
              <input id="task-when" autoFocus value={when} onChange={(e) => setWhen(e.target.value)} placeholder="Every Monday morning" className="bv-input" disabled={busy} />
              <label className="mt-3 flex items-center gap-2 text-sm">
                <input type="checkbox" checked={onlyWhenChanged} onChange={(e) => setOnlyWhenChanged(e.target.checked)} className="h-4 w-4 accent-[var(--bv-accent)]" />
                Only tell me when something changes
              </label>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button type="submit" className="bv-btn-primary" disabled={busy || !when.trim()}>
                  Schedule
                </button>
                <button type="button" className="bv-btn-quiet" onClick={() => setScheduling(false)}>
                  Cancel
                </button>
                {scheduleNote && (
                  <p className="text-sm" aria-live="polite">
                    {scheduleNote}
                  </p>
                )}
              </div>
            </form>
          )}
          {!scheduling && scheduleNote && (
            <p className="mt-3 text-sm" aria-live="polite">
              {scheduleNote}
            </p>
          )}
        </section>
      )}

      <div className="mt-10 flex flex-wrap items-baseline justify-between gap-4 text-sm">
        <Link to="/" className="bv-link">
          Ask something else
        </Link>
        {removing ? (
          <div role="group" aria-label="Remove this task" className="max-w-md">
            <p className="text-ink">{REMOVE_TASK_QUESTION}</p>
            <p className="bv-hint">{REMOVE_TASK_DETAIL}</p>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3">
              <button type="button" className="bv-link" disabled={busy} onClick={() => void removeTask()}>
                Yes, remove
              </button>
              <button type="button" className="text-muted hover:text-ink" onClick={() => setRemoving(false)}>
                Keep it
              </button>
            </div>
          </div>
        ) : (
          <button type="button" className="text-muted hover:text-ink" onClick={() => setRemoving(true)}>
            Remove from history
          </button>
        )}
      </div>
    </Page>
  );
}
