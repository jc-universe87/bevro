import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import PageHeader, { Page } from "../components/PageHeader";
import TaskStatus from "../components/TaskStatus";
import { api, ApiError, type Task } from "../lib/api";
import { relativeTime, statusOf } from "../lib/format";

const FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "open", label: "In progress" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Couldn't complete" },
];
const REFRESH_MS = 5000;
const OPEN = new Set(["created", "queued", "working", "waiting", "needs_input", "needs_approval", "scheduled", "monitoring"]);

/** What removing something actually does, said once, in the same words everywhere. */
export const REMOVE_TASK_QUESTION = "Remove this task and its results from Bevro?";
export const REMOVE_TASK_DETAIL = "This removes the task, what the agent did and anything it produced. The agent itself, and any scheduled work that asked for it, stay.";
export const CLEAR_HISTORY_QUESTION = "Remove all finished work from Bevro?";
export const CLEAR_HISTORY_DETAIL = "This clears Recent: every finished task and its results. Your apps and agents, scheduled work and settings stay. Anything still running is left alone.";

export default function Recent() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let again: ReturnType<typeof setTimeout> | undefined;
    const load = () =>
      api
        .listTasks({ q: query || undefined, state: filter && filter !== "open" ? filter : undefined })
        .then((list) => {
          if (cancelled) return;
          setTasks(list);
          // Anything still going: look again quietly, so finished work shows as finished.
          if (list.some((t) => OPEN.has(t.state))) again = setTimeout(load, REFRESH_MS);
        })
        .catch(() => !cancelled && setError("Recent work couldn't be loaded."));
    const handle = setTimeout(load, query ? 150 : 0);
    return () => {
      cancelled = true;
      clearTimeout(handle);
      if (again) clearTimeout(again);
    };
  }, [query, filter]);

  const visible = (tasks ?? []).filter((t) => (filter === "open" ? OPEN.has(t.state) : true));

  const remove = async (id: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.removeTask(id);
      setTasks((list) => (list ?? []).filter((t) => t.id !== id));
      setRemoving(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That couldn't be removed.");
    } finally {
      setBusy(false);
    }
  };

  const clearAll = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.clearHistory();
      setTasks(await api.listTasks({}));
      setClearing(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "History couldn't be cleared.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page>
      <PageHeader title="Recent" />
      <div className="flex flex-col sm:flex-row gap-2 mb-4">
        <label htmlFor="recent-search" className="sr-only">
          Search recent work
        </label>
        <input id="recent-search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" className="bv-input sm:max-w-xs" />
        <label htmlFor="recent-filter" className="sr-only">
          Filter by state
        </label>
        <select id="recent-filter" value={filter} onChange={(e) => setFilter(e.target.value)} className="bv-input sm:w-auto">
          {FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </div>

      {error && <p role="alert">{error}</p>}

      {clearing ? (
        <section aria-label="Clear history" className="mb-4 bv-panel">
          <p className="font-medium">{CLEAR_HISTORY_QUESTION}</p>
          <p className="bv-hint mt-1">{CLEAR_HISTORY_DETAIL}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className="bv-btn-primary" disabled={busy} onClick={() => void clearAll()}>
              Clear history
            </button>
            <button type="button" className="bv-btn-quiet" onClick={() => setClearing(false)}>
              Keep it
            </button>
          </div>
        </section>
      ) : (
        (tasks?.length ?? 0) > 0 && (
          <p className="mb-3 text-sm">
            <button type="button" className="text-muted hover:text-ink" onClick={() => setClearing(true)}>
              Clear history
            </button>
          </p>
        )
      )}
      {tasks && visible.length === 0 && !error && (
        <p className="bv-hint py-8">
          {query || filter ? "Nothing matches." : "Nothing yet. "}
          {!query && !filter && (
            <Link to="/" className="bv-link">
              Ask Bevro something.
            </Link>
          )}
        </p>
      )}

      <ul className="bv-divide" aria-label="Recent work">
        {visible.map((t) => {
          const status = statusOf(t);
          // The time that matters: when it finished, otherwise when it was asked.
          const when = t.completed_at ?? t.created_at;
          return (
          <li key={t.id}>
            <Link to={`/tasks/${t.id}`} className="block pt-3 -mx-2 px-2 rounded-md hover:bg-sunken">
              <div className="flex items-baseline justify-between gap-4">
                <span className="font-medium truncate min-w-0">{t.title}</span>
                <time dateTime={when} className="text-xs text-subtle shrink-0">
                  {relativeTime(when)}
                </time>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-muted">
                {t.provider && (
                  <span className="min-w-0 break-words">
                    {t.provider.name}
                    {t.provider.removed && <span className="text-subtle"> · Removed</span>}
                  </span>
                )}
                {t.provider && <span aria-hidden="true">·</span>}
                <TaskStatus task={t} />
                {status.kind === "completed" && (t.results ?? 0) > 0 && (
                  <>
                    <span aria-hidden="true">·</span>
                    <span>{t.results === 1 ? "1 result" : `${t.results} results`}</span>
                  </>
                )}
              </div>
              {status.kind === "completed" && t.summary && <p className="mt-1 text-sm text-muted truncate">{t.summary}</p>}
            </Link>
            <div className="-mx-2 px-2 pb-3 text-sm">
              {removing === t.id ? (
                <div role="group" aria-label="Remove this task">
                  <p className="text-ink">{REMOVE_TASK_QUESTION}</p>
                  <p className="bv-hint">{REMOVE_TASK_DETAIL}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-3">
                    <button type="button" className="bv-link" disabled={busy} onClick={() => void remove(t.id)}>
                      Yes, remove
                    </button>
                    <button type="button" className="text-muted hover:text-ink" onClick={() => setRemoving(null)}>
                      Keep it
                    </button>
                  </div>
                </div>
              ) : (
                <button type="button" className="text-muted hover:text-ink" onClick={() => setRemoving(t.id)}>
                  Remove from history
                </button>
              )}
            </div>
          </li>
          );
        })}
      </ul>
    </Page>
  );
}
