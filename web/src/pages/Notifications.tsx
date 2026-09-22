import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import PageHeader, { Page } from "../components/PageHeader";
import { api, ApiError, type Notification } from "../lib/api";

function ago(iso: string): string {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  return new Date(iso).toLocaleDateString([], { day: "numeric", month: "short" });
}

/** Only ever said when there is something to say: silence is the normal case. */
function deliveryWords(item: Notification): string | null {
  if (item.delivery === "delivery_failed") return item.delivery_problem ?? "Couldn't be sent.";
  if (item.delivery === "partly_delivered") return item.delivery_problem ?? "Only some of it was sent.";
  if (item.delivery === "sending") return item.delivery_problem ?? "Sending…";
  return null;
}

function Row({ item, onChange, onRemoved }: { item: Notification; onChange: (n: Notification) => void; onRemoved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setNote(null);
    try {
      await fn();
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const markRead = () => run(async () => onChange(await api.markNotificationRead(item.id)));
  const retry = () => run(async () => onChange(await api.retryNotification(item.id)));
  const problem = deliveryWords(item);

  return (
    <li className="py-4">
      <div className="flex items-baseline gap-2">
        {!item.read && <span className="h-2 w-2 shrink-0 rounded-full bg-accent" aria-hidden="true" />}
        <h2 className={item.read ? "font-medium text-muted" : "font-medium"}>{item.title}</h2>
        {!item.read && <span className="sr-only">Unread</span>}
      </div>
      {item.reason && <p className="text-sm">{item.reason}</p>}
      {item.summary && <p className="text-sm text-muted line-clamp-2">{item.summary}</p>}
      <p className="text-sm text-subtle">{ago(item.created_at)}</p>
      {problem && (
        <p className="text-sm mt-1" aria-live="polite">
          {problem}
        </p>
      )}

      <div className="mt-1.5 flex flex-wrap items-center gap-x-1 text-sm">
        {item.task_id && (
          <>
            {/* Opening the result is reading it: no second step. */}
            <Link to={`/tasks/${item.task_id}`} className="bv-link py-1" onClick={() => void api.markNotificationRead(item.id).catch(() => null)}>
              View result
            </Link>
            <span className="text-subtle" aria-hidden="true">·</span>
          </>
        )}
        {!item.read && (
          <>
            <button type="button" className="bv-link py-1" onClick={markRead} disabled={busy}>
              Mark read
            </button>
            <span className="text-subtle" aria-hidden="true">·</span>
          </>
        )}
        {item.delivery === "delivery_failed" && (
          <>
            <button type="button" className="bv-link py-1" onClick={retry} disabled={busy}>
              Try sending again
            </button>
            <span className="text-subtle" aria-hidden="true">·</span>
          </>
        )}
        {item.automation_id && (
          <>
            <Link to="/scheduled" className="bv-link py-1">
              Scheduled work
            </Link>
            <span className="text-subtle" aria-hidden="true">·</span>
          </>
        )}
        <button type="button" className="text-muted hover:text-ink py-1" onClick={() => void run(async () => { await api.dismissNotification(item.id); onRemoved(); })} disabled={busy}>
          Remove
        </button>
      </div>
      {note && (
        <p className="mt-1 text-sm" aria-live="polite">
          {note}
        </p>
      )}
    </li>
  );
}

export default function Notifications() {
  const [items, setItems] = useState<Notification[] | null>(null);
  const [unread, setUnread] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    api
      .listNotifications()
      .then((list) => {
        setItems(list.items);
        setUnread(list.unread);
      })
      .catch(() => setError("Notifications couldn't be loaded."));

  useEffect(() => {
    void load();
  }, []);

  const replace = (updated: Notification) => {
    setItems((list) => (list ?? []).map((n) => (n.id === updated.id ? updated : n)));
    void api.listNotifications(true).then((l) => setUnread(l.unread)).catch(() => null);
  };
  const drop = (id: string) => {
    setItems((list) => (list ?? []).filter((n) => n.id !== id));
    void api.listNotifications(true).then((l) => setUnread(l.unread)).catch(() => null);
  };

  const markAll = () =>
    api
      .markAllNotificationsRead()
      .then((list) => {
        setItems(list.items);
        setUnread(list.unread);
      })
      .catch(() => setError("That didn't work."));

  return (
    <Page>
      <PageHeader title="Notifications" />
      {error && <p role="alert">{error}</p>}
      {unread > 0 && (
        <p className="mb-3 text-sm">
          <button type="button" className="bv-link" onClick={markAll}>
            Mark all read
          </button>
        </p>
      )}
      {items && items.length === 0 && (
        <p className="bv-hint py-8">
          Nothing to tell you. Bevro says something when work it{"’"}s <Link to="/scheduled" className="bv-link">watching</Link> finds a result that matters.
        </p>
      )}
      <ul className="divide-y divide-line border-t border-b border-line empty:hidden" aria-label="Notifications">
        {(items ?? []).map((item) => (
          <Row key={item.id} item={item} onChange={replace} onRemoved={() => drop(item.id)} />
        ))}
      </ul>
    </Page>
  );
}
