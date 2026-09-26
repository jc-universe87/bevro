import { useId } from "react";
import { Link } from "react-router-dom";
import type { Provider, RouteAnswer } from "../lib/api";
import { here, hubView, type HubAction } from "../lib/hub";
import { ActionButton } from "../pages/Apps";
import Help from "./Help";

/**
 * What Bevro says about a request it hasn't started: which of the person's
 * apps and agents is for it, why, and the one thing to do next. Never an
 * error - a web app Bevro can't drive is an answer, not a failure.
 */
export default function GoalAnswer({
  answer,
  mine,
  busy,
  onUse,
  onChoose,
  onRetry,
}: {
  answer: RouteAnswer;
  mine: Provider[];
  busy: boolean;
  /** Start the work with this one: the person said so. */
  onUse: (id: string) => void;
  /** The person picked this one from a choice. */
  onChoose: (id: string) => void;
  /** Look again at whether it can be reached, and answer again. */
  onRetry: (id: string) => void;
}) {
  const messageId = useId();
  return (
    <section aria-labelledby={messageId} aria-live="polite" className="text-ink">
      <p id={messageId}>{answer.message}</p>
      {answer.outcome === "choice" && <Choices answer={answer} busy={busy} onChoose={onChoose} />}
      {answer.outcome === "none" && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Link to="/connect" className="bv-btn-primary">
            Connect something
          </Link>
          <Link to="/create" className="bv-btn-quiet">
            Create something new
          </Link>
        </div>
      )}
      {answer.item && <ForItem answer={answer} item={answer.item} provider={mine.find((p) => p.id === answer.item!.id)} busy={busy} onUse={onUse} onRetry={onRetry} />}
    </section>
  );
}

function Choices({ answer, busy, onChoose }: { answer: RouteAnswer; busy: boolean; onChoose: (id: string) => void }) {
  return (
    <ul className="mt-3 space-y-2" aria-label="Apps and agents that could help">
      {answer.choices.map((c) => (
        <li key={c.id}>
          <button type="button" className="bv-btn w-full justify-start text-left h-auto py-2.5" onClick={() => onChoose(c.id)} disabled={busy}>
            <span className="min-w-0">
              <span className="block font-semibold">{c.name}</span>
              {c.summary && <span className="block text-sm font-normal text-muted">{c.summary}</span>}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function ForItem({
  answer,
  item,
  provider,
  busy,
  onUse,
  onRetry,
}: {
  answer: RouteAnswer;
  item: { id: string; name: string };
  provider: Provider | undefined;
  busy: boolean;
  onUse: (id: string) => void;
  onRetry: (id: string) => void;
}) {
  const view = provider ? hubView(provider, here()) : null;
  const openApp: HubAction | null = view?.opening?.kind === "link" ? { id: "open", label: "Open app", href: view.opening.href } : null;
  // Only what this answer calls for; everything else is on the item's page.
  const actions: HubAction[] = (() => {
    switch (answer.outcome) {
      case "handoff":
        return view ? [view.primary] : [];
      case "blocked":
        return [{ id: "add_credential", label: "Add credential" }, { id: "how_to", label: "How to use it" }];
      case "unavailable":
        return [{ id: "retry", label: "Try again" }, ...(openApp ? [openApp] : [])];
      case "how_to":
        return [{ id: "how_to", label: "How to use it" }];
      case "setup":
        return [{ id: "setup_direct", label: "Set up direct access" }];
      default:
        return [];
    }
  })();
  return (
    <>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {answer.outcome === "direct" && (
          <button type="button" className="bv-btn-primary" onClick={() => onUse(item.id)} disabled={busy}>
            {busy ? "Starting…" : `Use ${item.name}`}
          </button>
        )}
        {provider &&
          actions.map((a, i) => (
            <ActionButton key={a.id} action={a} provider={provider} primary={i === 0} onAsk={() => onUse(item.id)} onRetry={() => onRetry(item.id)} busy={busy} />
          ))}
        {(!provider || answer.outcome === "direct") && (
          <Link to={`/apps/${item.id}`} className="bv-btn-quiet">
            More about {item.name}
          </Link>
        )}
      </div>
      {answer.why && (
        <Help question={`Why ${item.name}?`} className="mt-2">
          {answer.why}
        </Help>
      )}
    </>
  );
}
