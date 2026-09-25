import { Fragment, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import Icon from "../components/Icon";
import PageHeader, { Page } from "../components/PageHeader";
import { api, ApiError, type Provider, type ProviderDetails, type RemovalPlan } from "../lib/api";

function LetterMark({ provider }: { provider: Provider }) {
  const letter = provider.icon?.text ?? provider.name.slice(0, 1).toUpperCase();
  return (
    <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-line bg-surface text-sm font-semibold text-muted">
      {letter}
    </span>
  );
}

function statusNote(p: Provider): string | null {
  if (!p.enabled) return "Paused";
  if (p.connection === "declared" && !p.build) return "Not built yet";
  if (p.availability?.note) return p.availability.note;
  if (p.build && p.build.state !== "ready") return p.build.state === "failed" ? "Couldn't be built" : "Being built…";
  const missing = (p.credentials ?? []).filter((c) => !c.present);
  if (missing.length) return `Needs ${missing.map((c) => c.label.toLowerCase()).join(", ")}`;
  if (p.origin === "connected") return "Connected";
  return null;
}

const ADDRESS_KIND: Record<string, string> = {
  local_machine: "on this machine",
  private_network: "on a private network",
  vpn_overlay: "on a private overlay network",
  public_network: "on the public internet",
  unknown: "a name Bevro hasn't resolved",
};

const CONNECTED_FROM: Record<string, string> = { url: "a web address", mcp: "an MCP server", local: "a local folder", command: "a command" };

const CONNECTION_WORDS: Record<string, string> = { api: "API", mcp: "MCP server", command: "Local agent", local: "Local", declared: "Described, not built" };

function CredentialRow({ provider, credential, onChange, autoFocus }: { provider: Provider; credential: Provider["credentials"][number]; onChange: (p: Provider) => void; autoFocus: boolean }) {
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
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const inputId = `cred-${provider.id}-${credential.name}`;
  return (
    <li className="py-1">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span>
          {credential.label} · {credential.status ?? (credential.present ? "Added" : "Missing")}
        </span>
        {!editing && (
          <button type="button" className="bv-link text-sm" onClick={() => setEditing(true)}>
            {credential.source === "bevro" ? "Update" : credential.present ? "Override" : "Add"}
          </button>
        )}
      </div>
      {!editing && credential.note && <p className="bv-hint mt-0.5">{credential.note}</p>}
      {editing && (
        <form onSubmit={save} className="mt-2 flex flex-col sm:flex-row gap-2">
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
              ? "Only needed if you want Bevro to use a different value from the one the agent already has. Stored encrypted; never shown again."
              : "Stored encrypted on the server and passed to the agent only when it runs. It is never shown again."}
          </p>
        </form>
      )}
    </li>
  );
}

/** What removal really does, in the order that matters: what goes, then what stays. */
export function removalConsequences(provider: Provider, plan: RemovalPlan | null): string[] {
  const lines: string[] = [];
  if ((plan?.in_flight ?? 0) > 0) {
    return [`${provider.name} is working on something right now. Wait for it to finish, or cancel it first.`];
  }
  if (plan?.built_project) lines.push("The project Bevro wrote for it is deleted.");
  if (plan?.built_connection) lines.push("The connection Bevro built for it is deleted.");
  if ((plan?.credentials ?? 0) > 0) {
    lines.push(plan?.credentials === 1 ? "Its stored credential is deleted." : `Its ${plan?.credentials} stored credentials are deleted.`);
  }
  lines.push("Nothing outside Bevro is touched.");
  if ((plan?.history ?? 0) > 0) {
    const n = plan?.history ?? 0;
    lines.push(`${n} ${n === 1 ? "task stays" : "tasks stay"} in Recent, still showing ${provider.name} as having done the work.`);
  } else {
    lines.push("Any work it does from now on would stay in Recent; there is none yet.");
  }
  return lines;
}

function ManagePanel({ provider, onChange, onRemoved, focusCredential = false, autoTest = false }: { provider: Provider; onChange: (p: Provider) => void; onRemoved: () => void; focusCredential?: boolean; autoTest?: boolean }) {
  const [note, setNote] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  // What removal would actually cost, asked for before the question is put.
  const [plan, setPlan] = useState<RemovalPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [details, setDetails] = useState<ProviderDetails | null>(null);
  const [editing, setEditing] = useState(false);
  const [purpose, setPurpose] = useState(provider.build?.purpose ?? "");
  const testedOnOpen = useRef(false);

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

  const toggle = () => run(async () => onChange(await api.updateProvider(provider.id, { enabled: !provider.enabled })));
  const test = () =>
    run(async () => {
      const result = await api.checkProvider(provider.id);
      setNote(result.ok ? `Reachable.${result.detail ? ` ${result.detail}` : ""}` : `Not reachable${result.detail ? `: ${result.detail}` : "."}`);
    });
  const askToRemove = async () => {
    setPlan(await api.removalPlan(provider.id).catch(() => null));
    setConfirmRemove(true);
  };
  const remove = () =>
    run(async () => {
      await api.removeProvider(provider.id);
      onRemoved();
    });
  const reconnect = () =>
    run(async () => {
      onChange(await api.reconnectProvider(provider.id));
      setNote("Looked again and refreshed how it runs.");
    });
  const rebuild = () =>
    run(async () => {
      const result = await api.rebuildProvider(provider.id);
      setNote(result.detail ?? "Bevro is looking at the project again.");
      onChange(await api.listProviders().then((list) => list.find((p) => p.id === provider.id) ?? provider));
    });
  const rebuildAgent = (purpose?: string) =>
    run(async () => {
      const status = await api.createRebuild(provider.id, purpose);
      setNote(status.state === "failed" ? status.note : "Bevro is building a new version. The one you have keeps working until it passes.");
      setEditing(false);
      onChange(await api.listProviders().then((list) => list.find((p) => p.id === provider.id) ?? provider));
    });
  const loadDetails = async () => {
    if (details) return;
    try {
      setDetails(await api.providerDetails(provider.id));
    } catch {
      /* the summary stays */
    }
  };

  useEffect(() => {
    if (autoTest && !testedOnOpen.current) {
      testedOnOpen.current = true;
      void test();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoTest]);

  const capabilities = provider.capabilities.map((c) => c.title ?? c.id).join(", ");
  const missing = (provider.credentials ?? []).filter((c) => !c.present);
  return (
    <div className="mt-3 rounded-md border border-line bg-sunken/40 p-3 text-sm" aria-label={`Manage ${provider.name}`}>
      {(provider.details?.what_it_does || provider.details?.how_it_connects) && (
        <div className="mb-3 space-y-2">
          {provider.details?.what_it_does && (
            <div>
              <h3 className="text-muted">What it does</h3>
              <p>{provider.details.what_it_does}</p>
            </div>
          )}
          {provider.details?.how_it_connects && (
            <div>
              <h3 className="text-muted">How it connects</h3>
              <p>{provider.details.how_it_connects}</p>
            </div>
          )}
        </div>
      )}
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-muted">Connection</dt>
        <dd>{provider.enabled ? (provider.availability?.note ?? (missing.length ? `Needs ${missing.map((c) => c.label.toLowerCase()).join(", ")}` : "Available")) : "Paused"}</dd>
        {provider.runtime && (
          <>
            <dt className="text-muted">Runs via</dt>
            <dd>
              {provider.runtime.display_name}
              {provider.runtime.runs_at && <span className="text-subtle"> · {provider.runtime.runs_at}</span>}
            </dd>
          </>
        )}
        <dt className="text-muted">Can</dt>
        <dd>{capabilities || "Not specified"}</dd>
        {provider.build && (
          <>
            <dt className="text-muted">Purpose</dt>
            <dd>
              {editing ? (
                <form
                  className="flex flex-col gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (purpose.trim().length > 2) void rebuildAgent(purpose.trim());
                  }}
                >
                  <textarea value={purpose} onChange={(e) => setPurpose(e.target.value)} rows={3} className="bv-input resize-y" aria-label="What should this agent do?" />
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
                  {provider.build.purpose}{" "}
                  <button type="button" className="bv-link text-sm" onClick={() => setEditing(true)}>
                    Edit purpose
                  </button>
                </>
              )}
            </dd>
            <dt className="text-muted">Version</dt>
            <dd>
              {provider.build.version}
              {provider.build.state !== "ready" && <span className="text-subtle"> · {provider.build.state === "failed" ? "last build didn't pass" : "building a new version"}</span>}
            </dd>
          </>
        )}
        <dt className="text-muted">Credentials</dt>
        <dd>
          {(provider.credentials ?? []).length > 0 ? (
            <ul aria-label="Credentials">
              {provider.credentials.map((c) => (
                <CredentialRow key={c.name} provider={provider} credential={c} onChange={onChange} autoFocus={focusCredential} />
              ))}
            </ul>
          ) : (
            <span>{provider.runtime?.credentials_label ?? "None needed"}</span>
          )}
        </dd>
      </dl>
      {confirmRemove && (
        <div role="group" aria-label={`Remove ${provider.name}`} className="mt-3 rounded-md border border-line p-3">
          <p className="font-medium">Remove {provider.name} from Bevro?</p>
          <ul className="mt-1 space-y-0.5 text-sm text-muted">
            {removalConsequences(provider, plan).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" className="bv-btn" onClick={test} disabled={busy}>
          {(provider.runtime?.alternatives ?? 0) > 0 ? "Test all connections" : "Test"}
        </button>
        <button type="button" className="bv-btn" onClick={toggle} disabled={busy}>
          {provider.enabled ? "Pause" : "Resume"}
        </button>
        {provider.origin === "connected" && (
          <button type="button" className="bv-btn" onClick={reconnect} disabled={busy}>
            Reconnect
          </button>
        )}
        {provider.origin === "connected" && provider.runtime?.built && (
          <button type="button" className="bv-btn" onClick={rebuild} disabled={busy}>
            Rebuild connection
          </button>
        )}
        {provider.build?.can_rebuild && (
          <button type="button" className="bv-btn" onClick={() => rebuildAgent()} disabled={busy || editing}>
            Rebuild agent
          </button>
        )}
        {confirmRemove ? (
          <>
            <button type="button" className="bv-btn" onClick={remove} disabled={busy || (plan?.in_flight ?? 0) > 0}>
              Yes, remove
            </button>
            <button type="button" className="bv-btn-quiet" onClick={() => { setConfirmRemove(false); setPlan(null); }}>
              Keep
            </button>
          </>
        ) : (
          <button type="button" className="bv-btn-quiet" onClick={() => void askToRemove()} disabled={busy}>
            Remove
          </button>
        )}
        {note && (
          <p className="basis-full text-sm" aria-live="polite">
            {note}
          </p>
        )}
      </div>
      <details className="mt-3" onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && void loadDetails()}>
        <summary className="cursor-pointer text-muted hover:text-ink text-sm">Advanced details</summary>
        {details ? (
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs text-muted">
            <dt>Connection type</dt>
            <dd>{CONNECTION_WORDS[provider.connection ?? ""] ?? provider.connection ?? "—"}</dd>
            {details.runtimes
              .filter((r) => r.active)
              .map((r) => (
                <Fragment key={r.id}>
                  <dt>Selected</dt>
                  <dd>
                    {r.display_name} · {r.kind} via {r.adapter || "—"}
                  </dd>
                  <dt>Credential source</dt>
                  <dd>{r.credential_summary ?? r.credential_strategy.replaceAll("_", " ")}</dd>
                  {r.reachable_from && (
                    <>
                      <dt>Reachable from</dt>
                      <dd>{r.reachable_from}</dd>
                    </>
                  )}
                </Fragment>
              ))}
            {details.runtimes.some((r) => !r.active) && (
              <>
                <dt className="self-start">Other ways Bevro found</dt>
                <dd>
                  <ul className="space-y-0.5">
                    {Object.entries(
                      details.runtimes
                        .filter((r) => !r.active)
                        .reduce<Record<string, number>>((seen, r) => {
                          // Four entry points into one project are four ways
                          // in, and four identical lines say nothing.
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
            {details.source_kind && (
              <>
                <dt>Connected from</dt>
                <dd>{CONNECTED_FROM[details.source_kind] ?? `a ${details.source_kind}`}</dd>
              </>
            )}
            {details.source_name && (
              <>
                <dt>Calls itself</dt>
                <dd>{details.source_name}</dd>
              </>
            )}
            {details.source_target && (
              <>
                <dt>Address</dt>
                <dd className="break-all">{details.source_target}</dd>
              </>
            )}
            {details.location_class && (
              <>
                <dt>Kind of address</dt>
                <dd>{ADDRESS_KIND[details.location_class] ?? details.location_class}</dd>
              </>
            )}
            {details.runs_at && (
              <>
                <dt>Runs from</dt>
                <dd>{details.runs_at}</dd>
              </>
            )}
            {details.reachability && (
              <>
                <dt>Reached from</dt>
                <dd>
                  Bevro itself: {details.reachability.api ?? "unknown"} · this machine: {details.reachability.worker ?? "unknown"}
                </dd>
              </>
            )}
            {details.operation_count != null && (
              <>
                <dt>Operations</dt>
                <dd>{details.operation_count}</dd>
              </>
            )}
          </dl>
        ) : (
          <p className="mt-2 text-xs text-muted">Loading…</p>
        )}
        {details?.source_description && (
          <div className="mt-3">
            <dt className="text-xs text-muted">As the service describes itself</dt>
            <p className="mt-1 whitespace-pre-line text-xs text-subtle">{details.source_description}</p>
          </div>
        )}
      </details>
    </div>
  );
}

function AskForm({ provider, onDone }: { provider: Provider; onDone: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const request = text.trim();
    if (!request) return;
    setBusy(true);
    try {
      const task = await api.submitTask({ request, provider_id: provider.id });
      navigate(`/tasks/${task.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="mt-3 flex flex-col sm:flex-row gap-2">
      <label htmlFor={`ask-${provider.id}`} className="sr-only">
        Ask {provider.name}
      </label>
      <input
        id={`ask-${provider.id}`}
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={`Ask ${provider.name}...`}
        className="bv-input"
        disabled={busy}
      />
      <div className="flex gap-2">
        <button type="submit" className="bv-btn-primary" disabled={busy || !text.trim()}>
          Ask
        </button>
        <button type="button" className="bv-btn-quiet" onClick={onDone}>
          Cancel
        </button>
      </div>
      {error && (
        <p role="alert" className="text-sm sm:basis-full">
          {error}
        </p>
      )}
    </form>
  );
}

export default function Agents() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  const [searchParams] = useSearchParams();
  // A failed task's "Add credential" / "Test connection" lands here with the right panel open.
  const [managing, setManaging] = useState<string | null>(searchParams.get("manage"));
  const focusCredential = searchParams.get("credential") === "1";
  const autoTest = searchParams.get("test") === "1";
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listProviders().then(setProviders).catch(() => setError("Agents couldn't be loaded."));
  }, []);

  // Agents lists the agents you have. An agent Bevro ships that is not usable
  // on this installation is not one of them, so it is not listed at all - it
  // is offered where it is actually needed, such as on Create. Anything you
  // connected or created stays listed whatever its state today.
  const isUnusableBuiltIn = (p: Provider) => p.origin === "example" && p.availability?.state === "unavailable" && !p.actions.includes("ask");
  const mine = (providers ?? []).filter((p) => !isUnusableBuiltIn(p));

  const replace = (updated: Provider) => setProviders((list) => (list ?? []).map((x) => (x.id === updated.id ? updated : x)));
  const drop = (id: string) => {
    setProviders((list) => (list ?? []).filter((x) => x.id !== id));
    setManaging(null);
  };

  return (
    <Page>
      <PageHeader title="Agents">
        <div className="flex gap-2 text-sm">
          <Link to="/create" className="bv-btn">
            Create
          </Link>
          <Link to="/connect" className="bv-btn">
            Connect
          </Link>
        </div>
      </PageHeader>

      {error && <p role="alert">{error}</p>}
      <ul className="divide-y divide-line border-t border-b border-line empty:hidden" aria-label="Agents">
        {mine.map((p) => {
          const note = statusNote(p);
          return (
            <li key={p.id} className="py-4">
              <div className="flex items-start gap-3">
                <LetterMark provider={p} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <h2 className="font-medium">{p.name}</h2>
                    {note && <span className="text-xs text-subtle">{note}</span>}
                  </div>
                  <p className="text-sm text-muted">{p.description}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-1 text-sm">
                    {p.actions.includes("ask") && (
                      <button type="button" className="bv-link py-1" onClick={() => setAsking(asking === p.id ? null : p.id)} aria-expanded={asking === p.id}>
                        Ask
                      </button>
                    )}
                    {p.actions.includes("ask") && p.actions.includes("open") && <span className="text-subtle" aria-hidden="true">·</span>}
                    {p.actions.includes("open") && p.app_url && (
                      <a href={p.app_url} target="_blank" rel="noopener noreferrer" className="bv-link inline-flex items-center gap-1 py-1">
                        Open
                        <Icon name="external" size={14} />
                      </a>
                    )}
                    {p.origin !== "example" && (
                      <>
                        {p.actions.length > 0 && <span className="text-subtle" aria-hidden="true">·</span>}
                        <button type="button" className="text-muted hover:text-ink py-1" onClick={() => setManaging(managing === p.id ? null : p.id)} aria-expanded={managing === p.id}>
                          Manage
                        </button>
                      </>
                    )}
                  </div>
                  {asking === p.id && <AskForm provider={p} onDone={() => setAsking(null)} />}
                  {managing === p.id && <ManagePanel provider={p} onChange={replace} onRemoved={() => drop(p.id)} focusCredential={focusCredential} autoTest={autoTest} />}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
      {providers && mine.length === 0 && (
        <section aria-label="No agents yet" className="py-8">
          <p className="bv-hint">No agents yet.</p>
          <p className="bv-hint">Connect something you already have or create something new.</p>
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
    </Page>
  );
}
