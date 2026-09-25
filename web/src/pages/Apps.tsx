import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AskForm, LetterMark, OpenLink, StatusText } from "../components/Hub";
import PageHeader, { Page } from "../components/PageHeader";
import { api, type Provider } from "../lib/api";
import { here, hubView, type HubAction } from "../lib/hub";

/**
 * Apps & agents: everything the person has, whether or not Bevro can drive
 * it. Each one says what it is for, how it is used, and offers the one thing
 * most worth doing with it now.
 */

// Something Bevro ships that can't be used on this installation isn't one of
// the person's apps; it is offered where it is needed (Create), not listed.
export const isUnusableBuiltIn = (p: Provider) => p.origin === "example" && p.availability?.state === "unavailable" && !p.actions.includes("ask");

export function ActionButton({ action, provider, primary, onAsk, onRetry, onResume, onAddCredential, busy }: { action: HubAction; provider: Provider; primary: boolean; onAsk: () => void; onRetry: () => void; onResume?: () => void; onAddCredential?: () => void; busy?: boolean }) {
  const navigate = useNavigate();
  const cls = primary ? "bv-btn-primary" : "bv-btn-quiet";
  const detail = `/apps/${provider.id}`;
  switch (action.id) {
    case "open":
      return <OpenLink href={action.href!} label={action.label} primary={primary} className={primary ? "" : "bv-btn-quiet"} />;
    case "use":
      return (
        <button type="button" className={cls} onClick={onAsk}>
          {action.label}
        </button>
      );
    case "retry":
      return (
        <button type="button" className={cls} onClick={onRetry} disabled={busy}>
          {busy ? "Trying…" : action.label}
        </button>
      );
    case "add_credential":
      return (
        <button type="button" className={cls} onClick={onAddCredential ?? (() => navigate(`${detail}?credential=1`))}>
          {action.label}
        </button>
      );
    case "resume":
      if (onResume) {
        return (
          <button type="button" className={cls} onClick={onResume} disabled={busy}>
            {action.label}
          </button>
        );
      }
      return (
        <button type="button" className={cls} onClick={() => navigate(`${detail}#direct`)}>
          {action.label}
        </button>
      );
    case "setup_direct":
      return (
        <button type="button" className={cls} onClick={() => navigate(`${detail}#direct`)}>
          {action.label}
        </button>
      );
    default:
      return (
        <button type="button" className={cls} onClick={() => navigate(`${detail}#use`)}>
          {action.label}
        </button>
      );
  }
}

function HubItem({ provider, onChange }: { provider: Provider; onChange: (p: Provider) => void }) {
  const view = useMemo(() => hubView(provider, here()), [provider]);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const retry = async () => {
    setBusy(true);
    try {
      await api.checkProvider(provider.id);
      onChange(await api.getProvider(provider.id));
    } catch {
      /* the item keeps saying what was last known */
    } finally {
      setBusy(false);
    }
  };
  const nameId = `hub-${provider.id}`;
  return (
    <li className="py-5 first:pt-0" aria-labelledby={nameId}>
      <div className="flex items-start gap-4">
        <LetterMark provider={provider} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 id={nameId} className="text-base font-semibold">
              <Link to={`/apps/${provider.id}`} className="hover:underline underline-offset-2">
                {provider.name}
              </Link>
            </h2>
            {!provider.enabled && <StatusText tone="neutral">Paused</StatusText>}
            {provider.enabled && view.attention && <StatusText tone="attention">{view.attention}</StatusText>}
            {provider.enabled && view.progress && <StatusText tone="neutral">{view.progress}</StatusText>}
          </div>
          {provider.description && <p className="mt-0.5 text-muted max-w-prose">{provider.description}</p>}
          {view.through.length > 0 && (
            <p className="bv-meta mt-1.5">Available through {view.through.join(" · ")}</p>
          )}
          {view.directNote && (
            <p className="bv-meta">{view.directNote}</p>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <ActionButton action={view.primary} provider={provider} primary onAsk={() => setAsking((v) => !v)} onRetry={retry} busy={busy} />
            {view.secondary.map((a) => (
              <ActionButton key={a.id} action={a} provider={provider} primary={false} onAsk={() => setAsking((v) => !v)} onRetry={retry} busy={busy} />
            ))}
          </div>
          {asking && <AskForm provider={provider} onDone={() => setAsking(false)} />}
        </div>
      </div>
    </li>
  );
}

export default function Apps() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [error, setError] = useState(false);

  const load = () => {
    setError(false);
    api
      .listProviders()
      .then(setProviders)
      .catch(() => setError(true));
  };
  useEffect(load, []);

  const mine = (providers ?? []).filter((p) => !isUnusableBuiltIn(p));
  const replace = (updated: Provider) => setProviders((list) => (list ?? []).map((x) => (x.id === updated.id ? updated : x)));

  return (
    <Page>
      <PageHeader title="Apps & agents" lead={mine.length > 0 ? "Everything you use, what it's for, and how to get to it." : undefined}>
        {mine.length > 0 && (
          <div className="flex gap-2">
            <Link to="/connect" className="bv-btn">
              Connect
            </Link>
            <Link to="/create" className="bv-btn-quiet">
              Create
            </Link>
          </div>
        )}
      </PageHeader>

      {error && (
        <div role="alert" className="bv-panel">
          <p className="font-medium">Bevro couldn't load your apps and agents.</p>
          <p className="bv-hint mt-1">The server didn't answer. Nothing has been lost.</p>
          <button type="button" className="bv-btn mt-3" onClick={load}>
            Try again
          </button>
        </div>
      )}
      {!providers && !error && (
        <p className="bv-hint" aria-live="polite">
          Loading your apps and agents…
        </p>
      )}

      {mine.length > 0 && (
        <ul className="bv-divide" aria-label="Apps and agents">
          {mine.map((p) => (
            <HubItem key={p.id} provider={p} onChange={replace} />
          ))}
        </ul>
      )}

      {providers && mine.length === 0 && (
        <section aria-labelledby="empty-heading" className="py-10 max-w-prose">
          <h2 id="empty-heading" className="bv-heading">
            Your apps and agents will appear here.
          </h2>
          <p className="bv-lead mt-2">Connect something you already use, or create something new.</p>
          <div className="mt-5 flex flex-wrap gap-2">
            <Link to="/connect" className="bv-btn-primary">
              Connect
            </Link>
            <Link to="/create" className="bv-btn">
              Create
            </Link>
          </div>
        </section>
      )}
    </Page>
  );
}
