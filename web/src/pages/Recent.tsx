import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import PageHeader, { Page } from "../components/PageHeader";
import TaskStatus from "../components/TaskStatus";
import { api, type Task } from "../lib/api";
import { relativeTime } from "../lib/format";

const FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "open", label: "In progress" },
  { value: "completed", label: "Done" },
  { value: "failed", label: "Didn't finish" },
];
const OPEN = new Set(["created", "queued", "working", "waiting", "needs_input", "needs_approval", "scheduled", "monitoring"]);

export default function Recent() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const handle = setTimeout(() => {
      api
        .listTasks({ q: query || undefined, state: filter && filter !== "open" ? filter : undefined })
        .then((list) => !cancelled && setTasks(list))
        .catch(() => !cancelled && setError("Recent work couldn't be loaded."));
    }, query ? 150 : 0);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [query, filter]);

  const visible = (tasks ?? []).filter((t) => (filter === "open" ? OPEN.has(t.state) : true));

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

      <ul className="divide-y divide-line border-t border-b border-line" aria-label="Recent work">
        {visible.map((t) => (
          <li key={t.id}>
            <Link to={`/tasks/${t.id}`} className="block py-3 -mx-2 px-2 rounded-md hover:bg-sunken">
              <div className="flex items-baseline justify-between gap-4">
                <span className="font-medium truncate">{t.title}</span>
                <time dateTime={t.created_at} className="text-xs text-subtle shrink-0">
                  {relativeTime(t.created_at)}
                </time>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-muted">
                {t.provider && <span>{t.provider.name}</span>}
                {t.provider && <span aria-hidden="true">·</span>}
                <TaskStatus state={t.state} />
              </div>
              {t.summary && <p className="mt-1 text-sm text-muted truncate">{t.summary}</p>}
            </Link>
          </li>
        ))}
      </ul>
    </Page>
  );
}
