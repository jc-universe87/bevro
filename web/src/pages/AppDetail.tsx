import { Fragment, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import Help from "../components/Help";
import { AskForm, LetterMark, OpeningHelp, OpenLink, StatusText } from "../components/Hub";
import Icon from "../components/Icon";
import { Page } from "../components/PageHeader";
import { RemoveQuestion, useRemoval } from "../components/RemoveFromBevro";
import TestResults from "../components/TestResults";
import { api, ApiError, type Provider, type ProviderDetails, type TestResult } from "../lib/api";
import { here, hubView } from "../lib/hub";
import { ActionButton } from "./Apps";

/**
 * One app or agent. In order of what a person wants to know: what it is,
 * what it can do, how to use it, whether Bevro can send it work - and only
 * then the looking-after, which never competes with the useful action.
 */

const ADDRESS_KIND: Record<string, string> = {
  local_machine: "on this machine",
  private_network: "on a private network",
  vpn_overlay: "on a private overlay network",
  public_network: "on the public internet",
  unknown: "a name Bevro hasn't resolved",
};

const CONNECTED_FROM: Record<string, string> = { url: "a web address", mcp: "an MCP server", local: "a local folder", command: "a command" };

const CONNECTION_WORDS: Record<string, string> = { api: "API", mcp: "MCP server", command: "Local agent", local: "Local", declared: "Described, not built" };

function CredentialRow({ provider, credential, onChange, autoFocus, hideButton = false }: { provider: Provider; credential: Provider["credentials"][number]; onChange: (p: Provider) => void; autoFocus: boolean; hideButton?: boolean }) {
  const [editing, setEditing] = useState(!credential.present && autoFocus);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!value.trim()) return;
    setBusy(true);
    setError(null);
    try {
      onChange(await api.putSecret(provider.id, credential.name, value.trim()));
      setValue("");
      setEditing(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't save it. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const inputId = `cred-${provider.id}-${credential.name}`;
  return (
    <li className="py-1">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span>
          {credential.label} · {credential.status ?? (credential.present ? "Added" : "Missing")}
        </span>
        {!editing && !hideButton && (
          <button type="button" className="bv-link text-sm" onClick={() => setEditing(true)}>
            {credential.source === "bevro" ? "Update" : credential.present ? "Override" : "Add credential"}
          </button>
        )}
      </div>
      {!editing && credential.note && <p className="bv-hint mt-0.5">{credential.note}</p>}
      {!editing && credential.why && (
        <Help question="Why can't Bevro use the existing one?" className="mt-1">
          {credential.why}
        </Help>
      )}
      {editing && (
        <form onSubmit={save} className="mt-2 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <label htmlFor={inputId} className="sr-only">
            {credential.label}
          </label>
          <input id={inputId} type="password" autoComplete="off" autoFocus value={value} onChange={(e) => setValue(e.target.value)} className="bv-input sm:max-w-sm" disabled={busy} placeholder={credential.label} />
          <div className="flex gap-2">
            <button type="submit" className="bv-btn-primary" disabled={busy || !value.trim()}>
              Save
            </button>
            <button type="button" className="bv-btn-quiet" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
          {error && (
            <p role="alert" className="text-sm sm:basis-full">
              {error}
            </p>
          )}
          <p className="bv-hint sm:basis-full">
            {credential.present && credential.source !== "bevro"
              ? "Only needed if you want Bevro to use a different value from the one it already has. Stored encrypted; never shown again."
              : "Stored encrypted and given to it only when it runs a task for you. It is never shown again."}
          </p>
        </form>
      )}
    </li>
  );
}

/** "Which one is this connection for?" - its service serves several profiles
 * (workspaces, tenants...), and only the person can say. Asked once, kept. */
function ScopeChoice({ provider, onChosen }: { provider: Provider; onChosen: (p: Provider) => void }) {
  const choices = provider.direct?.choices ?? [];
  const [chosen, setChosen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const choose = async () => {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      onChosen(await api.chooseScope(provider.id, chosen));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <fieldset>
      <legend className="max-w-prose">{provider.direct?.note ?? "Bevro can send it work once you say which one this connection is for."}</legend>
      <div className="mt-3 space-y-1">
        {choices.map((c) => (
          <label key={c.value} className="flex items-center gap-2 cursor-pointer min-h-[36px]">
            <input type="radio" name={`scope-${provider.id}`} value={c.value} checked={chosen === c.value} onChange={() => setChosen(c.value)} className="accent-[var(--bv-accent)]" />
            <span className="break-words">{c.label}</span>
          </label>
        ))}
      </div>
      <button type="button" className="bv-btn-primary mt-3" onClick={() => void choose()} disabled={busy || !chosen}>
        {busy ? "Saving…" : "Use this one"}
      </button>
      {error && (
        <p role="alert" className="mt-2 text-sm">
          {error}
        </p>
      )}
    </fieldset>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-heading`} className="mt-10 scroll-mt-20">
      <h2 id={`${id}-heading`} className="bv-heading">
        {title}
      </h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function AdvancedDetails({ provider, details }: { provider: Provider; details: ProviderDetails | null }) {
  if (!details) return <p className="mt-2 text-xs text-muted">Loading…</p>;
  return (
    <>
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-xs text-muted sm:grid-cols-[auto_1fr]">
        <dt className="font-medium text-ink/80">Connection type</dt>
        <dd>{CONNECTION_WORDS[provider.connection ?? ""] ?? provider.connection ?? "None: Bevro doesn't send it work"}</dd>
        {provider.runtime && (
          <>
            <dt className="font-medium text-ink/80">Runs via</dt>
            <dd>
              {provider.runtime.display_name}
              {provider.runtime.runs_at && ` · ${provider.runtime.runs_at}`}
            </dd>
          </>
        )}
        {details.runtimes
          .filter((r) => r.active)
          .map((r) => (
            <Fragment key={r.id}>
              <dt className="font-medium text-ink/80">Selected</dt>
              <dd>
                {r.display_name} · {r.kind} via {r.adapter || "—"}
              </dd>
              <dt className="font-medium text-ink/80">Credential source</dt>
              <dd>{r.credential_summary ?? r.credential_strategy.replaceAll("_", " ")}</dd>
              {r.reachable_from && (
                <>
                  <dt className="font-medium text-ink/80">Reachable from</dt>
                  <dd>{r.reachable_from}</dd>
                </>
              )}
            </Fragment>
          ))}
        {details.runtimes.some((r) => !r.active) && (
          <>
            <dt className="self-start font-medium text-ink/80">Other ways Bevro found</dt>
            <dd>
              <ul className="space-y-0.5">
                {Object.entries(
                  details.runtimes
                    .filter((r) => !r.active)
                    .reduce<Record<string, number>>((seen, r) => {
                      // Four entry points into one project are four ways in,
                      // and four identical lines say nothing.
                      const line = `${r.display_name} · ${r.kind}${r.credential_summary ? ` — ${r.credential_summary}` : ""}${r.usable === false ? " — can't take work" : ""}`;
                      seen[line] = (seen[line] ?? 0) + 1;
                      return seen;
                    }, {}),
                ).map(([line, count]) => (
                  <li key={line}>
                    {line}
                    {count > 1 ? ` (${count})` : ""}
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}
        {(details.contexts ?? []).length > 0 && (
          <>
            <dt className="self-start font-medium text-ink/80">Execution context</dt>
            <dd>
              <ul className="space-y-1.5">
                {(details.contexts ?? []).map((c) => (
                  <li key={c.id}>
                    <div>
                      {c.title} · {c.name}
                      {c.available ? "" : " (not installed here)"}
                    </div>
                    <div>
                      Credentials: {c.credentials} · Can take ad-hoc work: {c.takes_work} · Needs administrator permission: {c.needs_admin} · Authorised: {c.authorised}
                    </div>
                    {c.why_not.length > 0 && <div>{c.why_not.join(" ")}</div>}
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}
        {details.source_kind && (
          <>
            <dt className="font-medium text-ink/80">Connected from</dt>
            <dd>{CONNECTED_FROM[details.source_kind] ?? `a ${details.source_kind}`}</dd>
          </>
        )}
        {details.source_name && (
          <>
            <dt className="font-medium text-ink/80">Calls itself</dt>
            <dd>{details.source_name}</dd>
          </>
        )}
        {details.source_target && (
          <>
            <dt className="font-medium text-ink/80">Address</dt>
            <dd className="break-all">{details.source_target}</dd>
          </>
        )}
        {details.location_class && (
          <>
            <dt className="font-medium text-ink/80">Kind of address</dt>
            <dd>{ADDRESS_KIND[details.location_class] ?? details.location_class}</dd>
          </>
        )}
        {details.runs_at && (
          <>
            <dt className="font-medium text-ink/80">Runs from</dt>
            <dd>{details.runs_at}</dd>
          </>
        )}
        {details.reachability && (
          <>
            <dt className="font-medium text-ink/80">Reached from</dt>
            <dd>
              Bevro itself: {details.reachability.api ?? "unknown"} · this machine: {details.reachability.worker ?? "unknown"}
            </dd>
          </>
        )}
        {details.operation_count != null && (
          <>
            <dt className="font-medium text-ink/80">Operations</dt>
            <dd>{details.operation_count}</dd>
          </>
        )}
        {provider.details?.how_it_connects && (
          <>
            <dt className="font-medium text-ink/80">How it connects</dt>
            <dd>{provider.details.how_it_connects}</dd>
          </>
        )}
      </dl>
      {details.source_description && (
        <div className="mt-3">
          <p className="text-xs font-medium text-ink/80">As it describes itself</p>
          <p className="mt-1 whitespace-pre-line text-xs text-subtle">{details.source_description}</p>
        </div>
      )}
    </>
  );
}

export default function AppDetail() {
  const { id = "" } = useParams();
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [provider, setProvider] = useState<Provider | null>(null);
  const [missing, setMissing] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [details, setDetails] = useState<ProviderDetails | null>(null);
  const removeButton = useRef<HTMLButtonElement>(null);
  const removal = useRemoval(id, () => navigate("/apps"), removeButton);
  const [editing, setEditing] = useState(false);
  const [purpose, setPurpose] = useState("");
  const [credentialOpen, setCredentialOpen] = useState(searchParams.get("credential") === "1");
  const autoTest = searchParams.get("test") === "1";
  const testedOnOpen = useRef(false);

  const load = () => {
    setLoadError(false);
    api
      .getProvider(id)
      .then((p) => {
        setProvider(p);
        setPurpose(p.build?.purpose ?? "");
      })
      .catch((err) => (err instanceof ApiError && err.status === 404 ? setMissing(true) : setLoadError(true)));
  };
  useEffect(load, [id]);

  // "#direct", "#use": arrive at the part that was asked about.
  useEffect(() => {
    if (!provider || !location.hash) return;
    document.getElementById(location.hash.slice(1))?.scrollIntoView?.({ block: "start" });
  }, [provider, location.hash]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setNote(null);
    try {
      await fn();
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : "Bevro couldn't reach the server. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };
  const refresh = async () => setProvider(await api.getProvider(id));
  const addCredential = () => {
    setCredentialOpen(true);
    document.getElementById("direct")?.scrollIntoView?.({ block: "start" });
  };
  const test = () =>
    run(async () => {
      setTestResult(await api.checkProvider(id));
      await refresh();
    });
  const toggle = () => run(async () => setProvider(await api.updateProvider(id, { enabled: !provider!.enabled })));
  const reconnect = () =>
    run(async () => {
      setProvider(await api.reconnectProvider(id));
      setNote("Bevro looked again and brought everything up to date.");
    });
  const rebuild = () =>
    run(async () => {
      const result = await api.rebuildProvider(id);
      setNote(result.detail ?? "Bevro is looking at the project again.");
      await refresh();
    });
  const rebuildAgent = (text?: string) =>
    run(async () => {
      const status = await api.createRebuild(id, text);
      setNote(status.state === "failed" ? status.note : "Bevro is building a new version. The one you have keeps working until it passes.");
      setEditing(false);
      await refresh();
    });
  const loadDetails = async () => {
    if (details) return;
    try {
      setDetails(await api.providerDetails(id));
    } catch {
      /* the page stays as it is */
    }
  };

  useEffect(() => {
    if (provider && autoTest && !testedOnOpen.current) {
      testedOnOpen.current = true;
      void test();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, autoTest]);

  if (missing) {
    return (
      <Page>
        <h1 className="bv-title">Not found</h1>
        <p className="bv-lead mt-2">This app or agent isn't in Bevro any more. It may have been removed.</p>
        <Link to="/apps" className="bv-btn mt-5">
          Back to Apps & agents
        </Link>
      </Page>
    );
  }
  if (loadError) {
    return (
      <Page>
        <div role="alert" className="bv-panel">
          <p className="font-medium">Bevro couldn't load this.</p>
          <p className="bv-hint mt-1">The server didn't answer. Nothing has been lost.</p>
          <button type="button" className="bv-btn mt-3" onClick={load}>
            Try again
          </button>
        </div>
      </Page>
    );
  }
  if (!provider) {
    return (
      <Page>
        <p className="bv-hint" aria-live="polite">
          Loading…
        </p>
      </Page>
    );
  }

  const p = provider;
  const view = hubView(p, here());
  const direct = p.direct?.state ?? (p.actions.includes("ask") ? "ready" : "not_set_up");
  const missingCreds = (p.credentials ?? []).filter((c) => !c.present);
  const present = (p.credentials ?? []).filter((c) => c.present);
  const others = (p.surfaces ?? []).filter((s) => s.kind !== "web_app");
  const capabilities = p.capabilities.map((c) => c.title ?? c.id);
  // The list says what it can do; a sentence made of the same words would only repeat it.
  const whatItDoes = p.capabilities.length === 0 && p.details?.what_it_does && p.details.what_it_does !== p.description ? p.details.what_it_does : null;
  const canLookAgain = p.origin === "connected";

  return (
    <Page>
      <Link to="/apps" className="bv-help -ml-1 mb-4">
        <Icon name="back" size={16} />
        Apps & agents
      </Link>

      <header className="flex items-start gap-4">
        <LetterMark provider={p} size="lg" />
        <div className="min-w-0 flex-1">
          <h1 className="bv-title">{p.name}</h1>
          {p.description && <p className="bv-lead mt-1">{p.description}</p>}
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            {!p.enabled && <StatusText tone="neutral">Paused</StatusText>}
            {p.enabled && view.attention && <StatusText tone="attention">{view.attention}</StatusText>}
            {p.enabled && view.progress && <StatusText tone="neutral">{view.progress}</StatusText>}
            {view.through.length > 0 && <span className="bv-meta">Available through {view.through.join(" · ")}</span>}
            {view.directNote && <span className="bv-meta">{view.directNote}</span>}
          </div>
        </div>
      </header>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <ActionButton action={view.primary} provider={p} primary onAsk={() => setAsking((v) => !v)} onRetry={test} onResume={toggle} onAddCredential={addCredential} busy={busy} />
        {view.secondary
          .filter((a) => a.id !== "how_to" && a.id !== "how_to_open")
          .map((a) => (
            <ActionButton key={a.id} action={a} provider={p} primary={false} onAsk={() => setAsking((v) => !v)} onRetry={test} onResume={toggle} onAddCredential={addCredential} busy={busy} />
          ))}
      </div>
      {asking && <AskForm provider={p} onDone={() => setAsking(false)} />}

      <Section id="can" title="What it can do">
        {p.build && (
          <div className="mb-3">
            {editing ? (
              <form
                className="flex flex-col gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (purpose.trim().length > 2) void rebuildAgent(purpose.trim());
                }}
              >
                <label htmlFor="purpose" className="bv-label">
                  What should this agent do?
                </label>
                <textarea id="purpose" value={purpose} onChange={(e) => setPurpose(e.target.value)} rows={3} className="bv-input resize-y" />
                <div className="flex gap-2">
                  <button type="submit" className="bv-btn-primary" disabled={busy || purpose.trim().length < 3}>
                    Save and rebuild
                  </button>
                  <button type="button" className="bv-btn-quiet" onClick={() => setEditing(false)}>
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <>
                <p>
                  {p.build.purpose}{" "}
                  <button type="button" className="bv-link text-sm" onClick={() => setEditing(true)}>
                    Edit purpose
                  </button>
                </p>
                <p className="bv-meta mt-1">
                  Version {p.build.version}
                  {p.build.state !== "ready" && ` · ${p.build.state === "failed" ? "the last build didn't pass; this version keeps working" : "building a new version"}`}
                </p>
              </>
            )}
          </div>
        )}
        {whatItDoes && <p className="max-w-prose">{whatItDoes}</p>}
        {capabilities.length > 0 ? (
          <ul className="mt-2 grid gap-x-8 gap-y-1.5 sm:grid-cols-2" aria-label="Capabilities">
            {capabilities.map((c) => (
              <li key={c} className="flex items-baseline gap-2">
                <span className="bv-dot bv-dot-neutral translate-y-[-1px]" aria-hidden="true" />
                {c}
              </li>
            ))}
          </ul>
        ) : (
          !whatItDoes && !p.build && <p className="bv-hint">Bevro hasn't been told what it can do yet.</p>
        )}
      </Section>

      <Section id="use" title="How you can use it">
        <ul className="space-y-4">
          {view.usable && (
            <li>
              <p>Ask Bevro for it, from Home or here. Bevro sends it the work and brings the result back.</p>
            </li>
          )}
          {view.web && (
            <li>
              <p>{view.web.sentence}</p>
              {view.opening?.kind === "link" ? (
                <div className="mt-2 flex flex-wrap items-center gap-3">
                  {![view.primary, ...view.secondary].some((a) => a.id === "open") && <OpenLink href={view.opening.href} label={`Open ${p.name}`} />}
                  <OpeningHelp provider={p} opening={view.opening} onChange={setProvider} />
                </div>
              ) : (
                <div className="mt-2">
                  <OpeningHelp provider={p} opening={view.opening} onChange={setProvider} />
                </div>
              )}
            </li>
          )}
          {others.map((s) => (
            <li key={`${s.kind}-${s.role}-${s.when ?? ""}`}>{s.sentence}</li>
          ))}
          {!view.usable && !view.web && others.length === 0 && <li className="bv-hint">Bevro doesn't know yet how you use it.</li>}
        </ul>
      </Section>

      <Section id="direct" title="Direct Bevro access">
        {direct === "ready" && <p className="max-w-prose">{p.details?.how_it_connects || p.direct?.note || "Bevro can send it work."}</p>}
        {direct === "paused" && (
          <>
            <p>{p.direct?.note ?? "Paused: Bevro won't send it work until you resume it."}</p>
            <button type="button" className="bv-btn mt-3" onClick={toggle} disabled={busy}>
              Resume
            </button>
          </>
        )}
        {direct === "needs_choice" && <ScopeChoice provider={p} onChosen={setProvider} />}
        {direct === "needs_credential" && (
          <>
            <p>{p.direct?.note ?? "Bevro needs a credential before it can send it work."}</p>
            {missingCreds.length > 0 && (
              <ul className="mt-2" aria-label="Missing credentials">
                <CredentialRow key={credentialOpen ? "open" : "closed"} provider={p} credential={missingCreds[0]} onChange={setProvider} autoFocus={credentialOpen} hideButton={view.primary.id === "add_credential"} />
              </ul>
            )}
          </>
        )}
        {(direct === "unreachable" || direct === "needs_start" || direct === "waiting_for_worker") && (
          <>
            <p>{p.direct?.note}</p>
            {view.primary.id !== "retry" && (
              <button type="button" className="bv-btn mt-3" onClick={test} disabled={busy}>
                {busy ? "Trying…" : "Try again"}
              </button>
            )}
            <Help question={direct === "waiting_for_worker" ? "What is the helper?" : "What can I check?"} className="mt-2">
              {direct === "waiting_for_worker"
                ? "Bevro reaches things on this computer through a small helper program. Start it with ./scripts/worker.sh (see the setup guide), then try again."
                : direct === "needs_start"
                  ? `${p.name} only answers while it's running. Bevro doesn't start or stop it. Start it as you normally do, then try again.`
                  : `Make sure ${p.name} is running and its address hasn't changed. Trying again changes nothing about it.`}
            </Help>
          </>
        )}
        {direct === "not_set_up" && (
          <>
            <p>
              {p.name} doesn't accept tasks from Bevro yet.
              {view.web || others.some((s) => s.role === "use") ? " You can still use it the ways above." : ""}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {canLookAgain && (
                <button type="button" className="bv-btn" onClick={reconnect} disabled={busy}>
                  {busy ? "Looking…" : "Look again"}
                </button>
              )}
              <Link to={`/connect/advanced?provider=${p.id}`} className="bv-btn-quiet">
                Set it up by hand
              </Link>
            </div>
            <Help question="Why can't Bevro use it directly?" className="mt-2">
              Bevro looked for a way for other programs to hand {p.name} a task, such as an API, and didn't find one. Its app is made for people, and that's fine. If {p.name} gains one later, Look again. If it
              has one Bevro didn't recognise, you can describe it by hand.
            </Help>
          </>
        )}
      </Section>

      <section aria-labelledby="care-heading" className="mt-14 border-t bv-sep pt-6">
        <h2 id="care-heading" className="bv-subheading text-muted">
          Settings
        </h2>

        {present.length > 0 && (
          <div className="mt-3 text-sm">
            <p className="text-muted">Credentials</p>
            <ul aria-label="Credentials">
              {present.map((c) => (
                <CredentialRow key={c.name} provider={p} credential={c} onChange={setProvider} autoFocus={false} />
              ))}
            </ul>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {direct !== "not_set_up" && (
            <button type="button" className="bv-btn" onClick={test} disabled={busy}>
              Test
            </button>
          )}
          {canLookAgain && direct !== "not_set_up" && (
            <button type="button" className="bv-btn" onClick={reconnect} disabled={busy}>
              Look again
            </button>
          )}
          {p.origin === "connected" && p.runtime?.built && (
            <button type="button" className="bv-btn" onClick={rebuild} disabled={busy}>
              Rebuild connection
            </button>
          )}
          {p.build?.can_rebuild && (
            <button type="button" className="bv-btn" onClick={() => rebuildAgent()} disabled={busy || editing}>
              Rebuild agent
            </button>
          )}
          {direct !== "not_set_up" && direct !== "paused" && (
            <button type="button" className="bv-btn-quiet" onClick={toggle} disabled={busy}>
              {p.enabled ? "Pause" : "Resume"}
            </button>
          )}
        </div>
        {note && (
          <p className="mt-3 text-sm" aria-live="polite">
            {note}
          </p>
        )}
        {testResult && <TestResults result={testResult} />}

        <details className="mt-6" onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && void loadDetails()}>
          <summary className="cursor-pointer text-sm text-muted hover:text-ink">Advanced details</summary>
          <AdvancedDetails provider={p} details={details} />
        </details>

        <div className="mt-10 border-t bv-sep pt-5">
          {removal.asking ? (
            <RemoveQuestion provider={p} removal={removal} />
          ) : (
            <button ref={removeButton} type="button" className="bv-btn-danger -ml-3" onClick={() => void removal.ask()} disabled={busy}>
              Remove from Bevro
            </button>
          )}
        </div>
      </section>
    </Page>
  );
}
