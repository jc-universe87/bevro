import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import GoalAnswer from "../components/GoalAnswer";
import { OpenLink } from "../components/Hub";
import Icon from "../components/Icon";
import { api, ApiError, type Notification, type Provider, type RouteAnswer, type ScheduleIntent } from "../lib/api";
import { here, hubView } from "../lib/hub";

const TIMEZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const SHOWN_ON_HOME = 6;

type HomeError = { message: string; reason: string | null; suggestion: { id: string; name: string } | null };

export default function Home() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HomeError | null>(null);
  // Which app or agent is for this, when Bevro isn't simply starting it.
  const [answer, setAnswer] = useState<RouteAnswer | null>(null);
  // When someone asks for work to happen again, Bevro shows what it would set
  // up and waits: recurring work is never created behind their back.
  const [intent, setIntent] = useState<ScheduleIntent | null>(null);
  // Anything Bevro found while the person was away. Quiet when there is none.
  const [waiting, setWaiting] = useState<Notification[]>([]);
  // The person's own apps and agents. None yet: say so honestly rather than
  // looking broken when a request comes back with nowhere to go.
  const [mine, setMine] = useState<Provider[] | null>(null);
  const hasAgents = mine === null ? null : mine.length > 0;
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    inputRef.current?.focus();
    api
      .listNotifications(true)
      .then((list) => setWaiting(list.items.slice(0, 3)))
      .catch(() => setWaiting([]));
    api
      .listProviders()
      .then(setMine)
      .catch(() => setMine(null));
  }, []);

  const failed = (err: unknown) => {
    if (err instanceof ApiError && err.answer) {
      // Not started, and why: the same answer Home would have shown.
      setAnswer(err.answer);
    } else {
      setError(err instanceof ApiError ? { message: err.message, reason: err.reason, suggestion: err.suggestion } : { message: "Bevro couldn't reach the server. Try again in a moment.", reason: null, suggestion: null });
    }
    setBusy(false);
  };

  /** Start the work. `provider_id` when the person chose who does it. */
  const start = async (request: string, provider_id?: string) => {
    setBusy(true);
    try {
      const task = await api.submitTask(provider_id ? { request, provider_id } : { request });
      navigate(`/tasks/${task.id}`);
    } catch (err) {
      failed(err);
    }
  };

  /**
   * Which app or agent, then how. Only a sure answer that Bevro can act on
   * starts work by itself; anything else is said, and waits for the person.
   * If the answer can't be had, the request goes as it always did: the task
   * service routes it the same way and says the same thing when it won't run.
   */
  const ask = async (request: string, provider_id?: string) => {
    setBusy(true);
    setError(null);
    setAnswer(null);
    const found = await api.route(request, provider_id).catch(() => null);
    if (found === null || (found.outcome === "direct" && found.sure)) {
      await start(request, provider_id);
      return;
    }
    setAnswer(found);
    setBusy(false);
  };

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
      await ask(request);
    } catch (err) {
      failed(err);
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
      failed(err);
    }
  };

  const justOnce = async () => {
    setIntent(null);
    await ask(text.trim());
  };

  // "Try again" on something Bevro couldn't reach: look, then answer afresh for it.
  const retry = async (id: string) => {
    setBusy(true);
    await api.checkProvider(id).catch(() => null);
    await ask(text.trim(), id);
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
        <h1 className="text-2xl md:text-3xl font-semibold tracking-tight text-center mb-6">What do you want to get done?</h1>
        <form onSubmit={submit} className="relative">
          <label htmlFor="ask" className="sr-only">
            What do you want to get done?
          </label>
          <textarea
            id="ask"
            ref={inputRef}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setIntent(null);
              setAnswer(null);
            }}
            onKeyDown={onKeyDown}
            placeholder="Say it in your own words…"
            rows={3}
            disabled={busy}
            className="bv-input resize-none rounded-lg px-4 py-3 pr-14 text-base md:text-lg shadow-sm"
          />
          <button
            type="submit"
            disabled={busy || !text.trim()}
            aria-label="Go"
            className="bv-btn-primary absolute right-2.5 bottom-2.5 h-11 w-11 sm:h-10 sm:w-10 px-0 rounded-md"
          >
            <Icon name="arrow" />
          </button>
        </form>
        {intent && (
          <section aria-label="Repeating work" className="mt-4 bv-panel">
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

        {hasAgents === false && !intent && (
          <section aria-label="Getting started" className="mt-4 bv-panel">
            <p className="font-medium">Start with the apps and agents you already use.</p>
            <p className="bv-hint mt-1">Connect them and Bevro learns what each one does, so it can send you to the right one.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Link to="/connect" className="bv-btn-primary">
                Connect
              </Link>
              <Link to="/create" className="bv-btn-quiet">
                Create
              </Link>
            </div>
          </section>
        )}

        {waiting.length > 0 && !intent && (
          <section aria-labelledby="attention-heading" className="mt-4 bv-panel">
            <h2 id="attention-heading" className="bv-subheading">
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

        <div className="mt-3 flex items-start justify-between gap-4 text-sm text-muted min-h-[1.5rem]">
          {answer ? (
            <GoalAnswer answer={answer} mine={mine ?? []} busy={busy} onUse={(id) => void start(text.trim(), id)} onChoose={(id) => void ask(text.trim(), id)} onRetry={(id) => void retry(id)} />
          ) : error ? (
            <HomeAnswer error={error} mine={mine ?? []} />
          ) : (
            <span />
          )}
          <span className="hidden sm:inline shrink-0 whitespace-nowrap text-subtle">Enter to send</span>
        </div>

        {mine && mine.length > 0 && !error && !answer && !intent && (
          <nav aria-label="Your apps and agents" className="mt-10 text-center">
            <p className="bv-meta">Your apps & agents</p>
            <ul className="mt-2 flex flex-wrap justify-center gap-x-1 gap-y-1">
              {mine.slice(0, SHOWN_ON_HOME).map((p) => (
                <li key={p.id}>
                  <Link to={`/apps/${p.id}`} className="bv-btn-quiet min-h-[36px] px-3 py-1 text-sm">
                    {p.name}
                  </Link>
                </li>
              ))}
              {mine.length > SHOWN_ON_HOME && (
                <li>
                  <Link to="/apps" className="bv-btn-quiet min-h-[36px] px-3 py-1 text-sm">
                    All {mine.length}
                  </Link>
                </li>
              )}
            </ul>
          </nav>
        )}
      </div>
    </div>
  );
}

/** What Bevro says when it can't take the request itself - and where to go instead, when it knows. */
function HomeAnswer({ error, mine }: { error: HomeError; mine: Provider[] }) {
  const target = error.suggestion ? mine.find((p) => p.id === error.suggestion!.id) : undefined;
  if (error.reason === "use_elsewhere" && error.suggestion) {
    const view = target ? hubView(target, here()) : null;
    const open = view?.opening?.kind === "link" ? view.opening.href : null;
    return (
      <div role="alert" className="text-ink">
        <p>{error.message}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {open && <OpenLink href={open} label={`Open ${error.suggestion.name}`} primary />}
          <Link to={`/apps/${error.suggestion.id}#use`} className={open ? "bv-btn-quiet" : "bv-btn"}>
            {open ? "More about it" : `How to open ${error.suggestion.name}`}
          </Link>
        </div>
      </div>
    );
  }
  return (
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
  );
}
