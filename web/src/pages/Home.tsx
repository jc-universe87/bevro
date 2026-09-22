import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import Icon from "../components/Icon";
import { api, ApiError, type Notification, type ScheduleIntent } from "../lib/api";

const TIMEZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

const SUGGESTION = "Allocate participants for the spring conference";

export default function Home() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; reason: string | null } | null>(null);
  // When someone asks for work to happen again, Bevro shows what it would set
  // up and waits: recurring work is never created behind their back.
  const [intent, setIntent] = useState<ScheduleIntent | null>(null);
  // Anything Bevro found while the person was away. Quiet when there is none.
  const [waiting, setWaiting] = useState<Notification[]>([]);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    inputRef.current?.focus();
    api
      .listNotifications(true)
      .then((list) => setWaiting(list.items.slice(0, 3)))
      .catch(() => setWaiting([]));
  }, []);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    const request = text.trim();
    if (!request || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (!intent) {
        // Asking whether this repeats must never stand between a person and
        // their work: if that check cannot be made, the task simply goes.
        const found = await api.scheduleIntent(request, TIMEZONE).catch(() => null);
        if (found?.recurring) {
          setIntent(found);
          setBusy(false);
          return;
        }
      }
      const task = await api.submitTask({ request });
      navigate(`/tasks/${task.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? { message: err.message, reason: err.reason } : { message: "Bevro couldn't reach the server.", reason: null });
      setBusy(false);
    }
  };

  const scheduleIt = async () => {
    if (!intent) return;
    setBusy(true);
    setError(null);
    try {
      await api.createAutomation({ when: text.trim(), instruction: intent.instruction ?? text.trim(), timezone: TIMEZONE });
      navigate("/scheduled");
    } catch (err) {
      setError(err instanceof ApiError ? { message: err.message, reason: err.reason } : { message: "Bevro couldn't reach the server.", reason: null });
      setBusy(false);
    }
  };

  const justOnce = async () => {
    setIntent(null);
    setBusy(true);
    try {
      const task = await api.submitTask({ request: text.trim() });
      navigate(`/tasks/${task.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? { message: err.message, reason: err.reason } : { message: "Bevro couldn't reach the server.", reason: null });
      setBusy(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  };

  return (
    <div className="flex min-h-[calc(100vh-3.5rem-5rem)] md:min-h-screen items-center justify-center px-4 sm:px-6">
      <div className="w-full max-w-prompt -mt-[8vh]">
        <h1 className="text-2xl md:text-3xl font-semibold tracking-tight text-center mb-6">What should we get done?</h1>
        <form onSubmit={submit} className="relative">
          <label htmlFor="ask" className="sr-only">
            Ask Bevro
          </label>
          <textarea
            id="ask"
            ref={inputRef}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setIntent(null);
            }}
            onKeyDown={onKeyDown}
            placeholder="Ask Bevro..."
            rows={3}
            disabled={busy}
            className="bv-input resize-none rounded-lg px-4 py-3 pr-14 text-base md:text-lg shadow-sm"
          />
          <button
            type="submit"
            disabled={busy || !text.trim()}
            aria-label="Ask"
            className="bv-btn-primary absolute right-2.5 bottom-2.5 h-10 w-10 px-0 rounded-md"
          >
            <Icon name="arrow" />
          </button>
        </form>
        {intent && (
          <section aria-label="Repeating work" className="mt-4 rounded-md border border-line bg-sunken/40 p-4">
            <p className="font-medium">{intent.title}</p>
            <p className="mt-0.5 text-sm text-muted">
              {intent.schedule}
              {intent.condition && <> · {intent.condition}</>}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button type="button" className="bv-btn-primary" onClick={scheduleIt} disabled={busy}>
                Schedule
              </button>
              <button type="button" className="bv-btn-quiet" onClick={justOnce} disabled={busy}>
                Just once
              </button>
            </div>
          </section>
        )}

        {waiting.length > 0 && !intent && (
          <section aria-labelledby="attention-heading" className="mt-4 rounded-md border border-line p-4">
            <h2 id="attention-heading" className="text-sm font-medium">
              Needs your attention
            </h2>
            <ul className="mt-2 space-y-2">
              {waiting.map((item) => (
                <li key={item.id} className="text-sm">
                  <Link to={item.task_id ? `/tasks/${item.task_id}` : "/notifications"} className="bv-link" onClick={() => void api.markNotificationRead(item.id).catch(() => null)}>
                    {item.title}
                  </Link>
                  {item.reason && <span className="text-muted"> — {item.reason}</span>}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-sm">
              <Link to="/notifications" className="bv-link">
                All notifications
              </Link>
            </p>
          </section>
        )}

        <div className="mt-3 flex items-center justify-between text-sm text-muted min-h-[1.5rem]">
          {error ? (
            <p role="alert" className="text-ink">
              {error.message}
              {error.reason === "no_provider" && (
                <>
                  {" "}
                  <Link to="/connect" className="bv-link">
                    Connect
                  </Link>
                  <span className="text-subtle"> · </span>
                  <Link to="/create" className="bv-link">
                    Create
                  </Link>
                </>
              )}
            </p>
          ) : (
            <p>
              <span className="hidden sm:inline">Try </span>
              <button type="button" className="bv-link" onClick={() => { setText(SUGGESTION); inputRef.current?.focus(); }}>
                “{SUGGESTION}”
              </button>
            </p>
          )}
          <span className="hidden sm:inline text-subtle">Enter to send</span>
        </div>
      </div>
    </div>
  );
}
