import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ArtifactView from "../components/ArtifactView";
import { OpenLink } from "../components/Hub";
import Icon from "../components/Icon";
import { Page } from "../components/PageHeader";
import { api, ApiError, isTerminal, type Provider, type TaskDetail } from "../lib/api";
import { elapsed, fullDateTime, statusOf } from "../lib/format";
import { here, hubView } from "../lib/hub";
import { REMOVE_TASK_DETAIL, REMOVE_TASK_QUESTION } from "./Recent";

/** Where a failure's actions lead. add_credential opens Manage for that provider with the field ready. */
function actionTarget(kind: string, providerId: string): string {
  if (kind === "add_credential") return `/apps/${providerId}?credential=1`;
  if (kind === "test_connection") return `/apps/${providerId}?test=1`;
  return `/apps/${providerId}`;
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

/** Shape as well as words: never colour alone. */
function StatusMark({ kind }: { kind: string }) {
  if (kind === "starting" || kind === "working") return <span className="bv-pulse mt-2 inline-block h-2.5 w-2.5 shrink-0 rounded-pill bg-accent" aria-hidden="true" />;
  if (kind === "completed") return <Icon name="check" size={18} className="mt-1 shrink-0 text-accent" aria-hidden="true" />;
  return <span className="mt-2 inline-block h-2.5 w-2.5 shrink-0 rounded-pill border-2 border-current text-muted" aria-hidden="true" />;
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
  // "Try again" when the app may already have acted on the request: asked first.
  const [confirmRetry, setConfirmRetry] = useState(false);
  const [cancelNote, setCancelNote] = useState<string | null>(null);
  // The app itself, for a way to open it when something went wrong.
  const [app, setApp] = useState<Provider | null>(null);

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
    setCancelNote(null);
    try {
      setTask(await api.cancelTask(task.id));
    } catch (err) {
      // Refused honestly (it can't be stopped from here), or the poll will show where it really is.
      if (err instanceof ApiError && err.status === 409) setCancelNote(err.message);
    }
  };

  const failedProviderId = task?.state === "failed" ? task.runs[task.runs.length - 1]?.provider.id ?? null : null;
  useEffect(() => {
    if (!failedProviderId) return;
    api
      .getProvider(failedProviderId)
      .then(setApp)
      .catch(() => setApp(null));
  }, [failedProviderId]);

  const retry = async () => {
    if (!task || retrying) return;
    setConfirmRetry(false);
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
      const again = await api.submitTask({ request: task.original_request, provider_id: task.provider?.id ?? undefined });
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

  const status = statusOf(task);
  const active = !isTerminal(task.state);
  const waitingForYou = task.state === "needs_input" && task.input_request;
  const run = task.runs[task.runs.length - 1];
  const failure = task.state === "failed" ? run?.failure ?? null : null;
  const name = run?.provider.name ?? task.provider?.name ?? "the app";
  const primary = task.artifacts.find((a) => a.primary) ?? task.artifacts[0];
  const more = task.artifacts.filter((a) => a !== primary);
  const hasResult = !failure && (Boolean(task.summary) || task.artifacts.length > 0) && !active;
  const steps = run?.steps ?? [];
  const showSteps = active && !waitingForYou && steps.length > 1;
  // Only a complete record can say where its app opens; anything less offers no link.
  const opening = app && Array.isArray(app.actions) ? hubView(app, here()).opening : null;
  const openHref = opening?.kind === "link" ? opening.href : null;

  return (
    <Page narrow>
      <nav className="text-sm text-muted mb-6" aria-label="Breadcrumb">
        <Link to="/recent" className="hover:text-ink">
          Recent
        </Link>
        <span aria-hidden="true"> / </span>
        <span className="text-ink break-words">{task.title}</span>
      </nav>

      <section aria-label="Request" className="mb-6">
        <h1 className="text-lg md:text-xl leading-snug font-normal break-words">{task.original_request}</h1>
        <p className="bv-hint mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
          {task.provider && (
            <span>
              {task.provider.name}
              {task.provider.removed && <span className="text-subtle"> · Removed</span>}
            </span>
          )}
          {task.provider && <span aria-hidden="true">·</span>}
          <time dateTime={task.created_at}>{fullDateTime(task.created_at)}</time>
        </p>
        {run?.workspace && run.permissions.length > 0 && (
          <p className="bv-hint mt-1 text-xs">
            {run.provider.name} can: {run.permissions.join(" · ")}
          </p>
        )}
      </section>

      {/* Where it is, in one sentence. The only part announced as it changes;
          what the app says it is doing, and the time, are not read out each poll. */}
      <section aria-label="Status" className="border-t bv-sep pt-6">
        <p aria-live="polite" className="flex items-start gap-2.5 text-base md:text-lg">
          <StatusMark kind={status.kind} />
          <span>{status.headline}</span>
        </p>
        {status.note && <p className="bv-hint mt-1 pl-6">{status.note}</p>}
        {status.kind === "working" && status.since && !status.quiet && <p className="bv-hint mt-1 pl-6">Started {elapsed(status.since)} ago</p>}
        {(status.can_cancel || cancelNote) && (
          <div className="mt-3 pl-6">
            {status.can_cancel && (
              <button type="button" onClick={cancel} className="bv-btn-quiet -ml-3">
                Cancel
              </button>
            )}
            {cancelNote && (
              <p className="bv-hint" role="status">
                {cancelNote}
              </p>
            )}
          </div>
        )}
        {status.quiet && (
          <div className="mt-3 pl-6 flex flex-wrap gap-2">
            <button type="button" className="bv-btn" onClick={() => setAnswered((v) => !v)}>
              Check again
            </button>
            {run?.provider.id && (
              <Link to={`/apps/${run.provider.id}`} className="bv-btn-quiet">
                Go to {name}
              </Link>
            )}
          </div>
        )}

        {failure && run && (
          <div className="mt-4 pl-6">
            {confirmRetry ? (
              <div role="group" aria-labelledby="retry-question" className="bv-panel">
                <p id="retry-question">Try again may send this request to {name} again.</p>
                <p className="bv-hint mt-1">It may already have done some of the work before it stopped.</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button type="button" className="bv-btn-primary" onClick={retry} disabled={retrying}>
                    {retrying ? "Trying again…" : "Try again anyway"}
                  </button>
                  <button type="button" className="bv-btn-quiet" onClick={() => setConfirmRetry(false)}>
                    Leave it
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                {failure.actions.map((a, i) => {
                  const cls = i === 0 ? "bv-btn-primary" : "bv-btn";
                  if (a.kind === "retry") {
                    return (
                      <button key={a.kind} type="button" onClick={() => (failure.may_repeat ? setConfirmRetry(true) : void retry())} disabled={retrying} className={cls}>
                        {retrying ? "Trying again…" : a.label}
                      </button>
                    );
                  }
                  if (a.kind === "open_app") return openHref ? <OpenLink key={a.kind} href={openHref} label={a.label} primary={i === 0} className={i === 0 ? "" : "bv-btn"} /> : null;
                  return run.provider.id ? (
                    <Link key={a.kind} to={actionTarget(a.kind, run.provider.id)} className={cls}>
                      {a.label}
                    </Link>
                  ) : null;
                })}
              </div>
            )}
            {retryError && (
              <p role="alert" className="mt-2 text-sm">
                {retryError}
              </p>
            )}
          </div>
        )}
      </section>

      {waitingForYou && task.input_request && (
        <section aria-label="Question" className="mt-6 border-t bv-sep pt-6">
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
        <section aria-label="What it has done so far" className="mt-6 border-t bv-sep pt-6">
          <ol className="space-y-1 text-sm">
            {steps.map((step, i) => (
              <li key={i} className={i === steps.length - 1 ? "text-ink" : "text-muted"}>
                {step}
              </li>
            ))}
          </ol>
        </section>
      )}

      {hasResult && (
        <section aria-label="Result" className="mt-6 border-t bv-sep pt-6">
          {task.summary && <p className="text-base md:text-lg break-words">{task.summary}</p>}
          {primary && (
            <div className={task.summary ? "mt-5" : ""}>
              <ArtifactView artifact={primary} />
            </div>
          )}
          {more.length > 0 && (
            <>
              <h2 className="bv-subheading mt-8">More results</h2>
              <ul className="mt-3 space-y-5">
                {more.map((a) => (
                  <li key={a.id}>
                    <ArtifactView artifact={a} />
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      {failure && task.artifacts.length > 0 && (
        <section aria-label="What it produced before it stopped" className="mt-6 border-t bv-sep pt-6">
          <ul className="space-y-5">
            {task.artifacts.map((a) => (
              <li key={a.id}>
                <ArtifactView artifact={a} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {run && (
        <details className="mt-8 text-sm">
          <summary className="cursor-pointer text-muted hover:text-ink">Details</summary>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-muted">
            <dt>App</dt>
            <dd className="break-words">
              {run.provider.name}
              {run.provider.removed && " (removed since)"}
            </dd>
            <dt>Asked</dt>
            <dd>{fullDateTime(task.created_at)}</dd>
            {run.started_at && (
              <>
                <dt>Started</dt>
                <dd>{fullDateTime(run.started_at)}</dd>
              </>
            )}
            {run.completed_at && (
              <>
                <dt>Finished</dt>
                <dd>{fullDateTime(run.completed_at)}</dd>
              </>
            )}
          </dl>
          {task.runs.some((r) => (r.tried ?? []).length > 0) || task.runs.length > 1 ? (
            <ol className="mt-4 space-y-3" aria-label="Attempts">
              {task.runs.map((r, i) => (
                <li key={r.id}>
                  <p className="text-ink">
                    {task.runs.length > 1 ? `Attempt ${i + 1}` : "How Bevro reached it"}
                    {r.completed_at && <span className="text-muted"> · {fullDateTime(r.completed_at)}</span>}
                  </p>
                  {(r.tried ?? []).length > 0 && (
                    <ul className="mt-1 space-y-0.5 text-muted">
                      {(r.tried ?? []).map((t, j) => (
                        <li key={j} className="break-words">
                          {t.way}: {t.outcome}
                        </li>
                      ))}
                    </ul>
                  )}
                  {r.state === "failed" && r.error_summary && <p className="mt-1 text-muted break-words">What happened: {r.error_summary}</p>}
                </li>
              ))}
            </ol>
          ) : null}
          <p className="mt-4 text-xs text-subtle break-all">Reference {run.id}</p>
        </details>
      )}

      {task.state === "completed" && (
        <section aria-label="Next" className="mt-8 border-t bv-sep pt-6">
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
