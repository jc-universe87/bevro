import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import Icon from "../components/Icon";
import PageHeader, { Page } from "../components/PageHeader";
import { api, ApiError, type BridgeStatus, type ConnectDraft, type DraftView } from "../lib/api";

const POLL_MS = 800;

function readiness(draft: DraftView): string {
  switch (draft.availability) {
    case "ready":
      return "Ready";
    case "needs_worker":
      return "Runs on this machine";
    case "needs_start":
      return "Not running yet";
    default:
      return "Can't be used yet";
  }
}

function capabilityText(draft: DraftView): string {
  return draft.capabilities.map((c) => c.title ?? c.id).join(", ");
}

export default function Connect() {
  const [target, setTarget] = useState("");
  const [draft, setDraft] = useState<ConnectDraft | null>(null);
  const [connected, setConnected] = useState<{ name: string; needsWorker: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Editable parts of the confirmation: shown only when discovery is unsure, or for a credential.
  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [secret, setSecret] = useState("");
  const [runtimeId, setRuntimeId] = useState<string | null>(null);
  // Which profile, workspace or tenant this connection is for, when asked.
  const [scope, setScope] = useState<string | null>(null);
  const [testNote, setTestNote] = useState<string | null>(null);
  const [bridge, setBridge] = useState<BridgeStatus | null>(null);
  const pollRef = useRef<number | null>(null);
  const bridgeRef = useRef<number | null>(null);

  const stopPolling = () => {
    for (const ref of [pollRef, bridgeRef]) {
      if (ref.current !== null) {
        window.clearTimeout(ref.current);
        ref.current = null;
      }
    }
  };

  /** Follow a connection being built: plain steps, nothing technical. */
  const followBridge = (status: BridgeStatus) => {
    setBridge(status);
    if (status.state === "ready" || status.state === "failed") {
      setBusy(false);
      return;
    }
    bridgeRef.current = window.setTimeout(async () => {
      try {
        followBridge(await api.bridgeStatus(status.provider_id));
      } catch {
        setError("Bevro couldn't reach the server.");
        setBusy(false);
      }
    }, 2000);
  };

  const makeConnectable = async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      followBridge(await api.connectBridge(draft.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
      setBusy(false);
    }
  };
  useEffect(() => stopPolling, []);

  const adopt = (d: ConnectDraft) => {
    setDraft(d);
    if (d.draft) {
      setName(d.draft.name);
      setSummary(capabilityText(d.draft));
      setRuntimeId(d.draft.choice_needed ? null : d.draft.runtime?.id ?? null);
    }
    if (d.state === "looking" || d.state === "testing") {
      pollRef.current = window.setTimeout(async () => {
        try {
          adopt(await api.connectDraft(d.id));
        } catch {
          setError("Bevro couldn't reach the server.");
        }
      }, POLL_MS);
    } else {
      setBusy(false);
      if (d.test) setTestNote(d.test.ok ? `Test passed. ${d.test.detail ?? ""}`.trim() : `Test failed: ${d.test.detail ?? "not reachable."}`);
    }
  };

  const discover = async (e: FormEvent) => {
    e.preventDefault();
    const text = target.trim();
    if (!text || busy) return;
    stopPolling();
    setBusy(true);
    setError(null);
    setDraft(null);
    setConnected(null);
    setSecret("");
    setTestNote(null);
    setBridge(null);
    try {
      adopt(await api.connectDiscover(text));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
      setBusy(false);
    }
  };

  const secrets = () => (draft?.draft?.auth.secret_name && secret.trim() ? { [draft.draft.auth.secret_name]: secret.trim() } : {});

  const test = async () => {
    if (!draft) return;
    setBusy(true);
    setTestNote(null);
    setError(null);
    try {
      adopt(await api.connectTest(draft.id, secrets()));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!draft?.draft) return;
    setBusy(true);
    setError(null);
    try {
      const body: Parameters<typeof api.connectConfirm>[1] = { secrets: secrets() };
      if (draft.draft.choice_needed && runtimeId) body.runtime_id = runtimeId;
      if (scope) body.scope = scope;
      if (name.trim() && name.trim() !== draft.draft.name) body.name = name.trim();
      if (summary.trim() !== capabilityText(draft.draft)) body.capability_summary = summary.trim();
      const provider = await api.connectConfirm(draft.id, body);
      setConnected({ name: provider.name, needsWorker: draft.draft.availability === "needs_worker" });
      setDraft(null);
      setTarget("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const found = draft?.state === "found" || draft?.state === "testing" ? draft.draft : null;
  const unsure = found?.confidence === "low";

  return (
    <Page narrow>
      <PageHeader title="Connect" />
      <form onSubmit={discover} aria-label="Connect">
        <label htmlFor="connect-target" className="text-lg font-medium block mb-3">
          Connect an agent, app or service to Bevro.
        </label>
        <input
          id="connect-target"
          autoFocus
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="URL, local project, MCP server or command"
          className="bv-input py-3"
          autoComplete="off"
          spellCheck={false}
          disabled={busy && draft?.state === "looking"}
        />
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
          <button type="submit" className="bv-btn-primary" disabled={busy || !target.trim()}>
            {busy && draft?.state === "looking" ? "Looking…" : "Connect"}
          </button>
          <Link to="/connect/advanced" className="bv-link text-sm">
            Advanced setup
          </Link>
        </div>
      </form>

      {error && (
        <p role="alert" className="mt-4 text-sm">
          {error}
        </p>
      )}

      {draft?.state === "looking" && (
        <p className="mt-6 flex items-center gap-2 text-sm text-muted" aria-live="polite">
          <span className="bv-pulse inline-block h-2 w-2 rounded-full bg-accent" aria-hidden="true" />
          Looking at {draft.target_label}…
        </p>
      )}

      {draft?.state === "failed" && (
        <section aria-label="Not connected" className="mt-6 border-t border-line pt-5">
          <p role="alert" className="text-sm">
            {draft.error}
          </p>
          <p className="bv-hint mt-2">
            If you know how it works, <Link to="/connect/advanced" className="bv-link">Advanced setup</Link> lets you describe it yourself.
          </p>
        </section>
      )}

      {found && (
        <section aria-label="Found" className="mt-8 border-t border-line pt-6">
          <p className="text-xs uppercase tracking-wide text-subtle mb-2">Found</p>
          {unsure ? (
            <div className="mb-2">
              <label htmlFor="connect-name" className="sr-only">
                Name
              </label>
              <input id="connect-name" value={name} onChange={(e) => setName(e.target.value)} className="bv-input max-w-sm text-xl font-semibold" maxLength={120} />
            </div>
          ) : (
            <h2 className="text-xl font-semibold tracking-tight">{found.name}</h2>
          )}
          {found.description && <p className="mt-1 text-muted">{found.description}</p>}

          <div className="mt-4">
            {unsure ? (
              <>
                <p className="text-sm mb-2">{found.note}</p>
                <label htmlFor="connect-summary" className="bv-label">
                  What can it do?
                </label>
                <input id="connect-summary" value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="Research, Product strategy, Competitor analysis" className="bv-input" />
                <p className="bv-hint mt-1">A few words, separated by commas. This is how Bevro decides what to send here.</p>
              </>
            ) : (
              <>
                <p className="text-sm text-muted mb-1">Can:</p>
                <ul className="space-y-0.5" aria-label="Capabilities">
                  {found.capabilities.map((c) => (
                    <li key={c.id} className="text-sm">
                      • {c.title ?? c.id}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          {found.scope_choices && found.scope_choices.length > 0 ? (
            <fieldset className="mt-4">
              <legend className="text-sm text-muted mb-1">
                I found {found.scope_choices.length}. Which should this connection use?
              </legend>
              <div className="space-y-1">
                {found.scope_choices.map((c) => (
                  <label key={c.value} className="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="radio" name="scope" value={c.value} checked={scope === c.value} onChange={() => setScope(c.value)} className="accent-[var(--bv-accent)]" />
                    {c.label}
                  </label>
                ))}
              </div>
            </fieldset>
          ) : (
            found.connected_for && (
              <p className="mt-4 text-sm">
                <span className="text-muted">Connected for:</span> {found.connected_for}
              </p>
            )
          )}

          {found.choice_needed && found.runtime_options.length > 1 ? (
            <fieldset className="mt-4">
              <legend className="text-sm text-muted mb-1">I found two ways to connect this. Which should Bevro use?</legend>
              <div className="space-y-1">
                {found.runtime_options.map((o) => (
                  <label key={o.id} className="flex items-start gap-2 text-sm cursor-pointer">
                    <input type="radio" name="runtime" value={o.id} checked={runtimeId === o.id} onChange={() => setRuntimeId(o.id)} className="mt-1 accent-[var(--bv-accent)]" />
                    <span>
                      {o.display_name}
                      <span className="text-subtle"> · {o.credentials.label === "Missing" ? "needs a credential" : o.credentials.label.toLowerCase()}</span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
          ) : (
            found.runs_via && (
              <p className="mt-4 text-sm">
                <span className="text-muted">Runs via:</span> {found.runs_via}
                <span className="text-subtle"> · {readiness(found)}</span>
                {found.confidence !== "high" && !unsure && <span className="text-subtle"> · {found.confidence_label}</span>}
              </p>
            )
          )}

          {found.warnings.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm" aria-label="Notes">
              {found.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}

          {!found.auth.required && found.auth.hint && <p className="mt-2 text-sm text-muted">{found.auth.hint}</p>}

          {found.auth.required && (
            <div className="mt-5">
              <p className="font-medium text-sm mb-2">Authentication required</p>
              <label htmlFor="connect-secret" className="bv-label">
                {found.auth.label ?? "API token"}
              </label>
              <input id="connect-secret" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} className="bv-input max-w-sm" />
              {found.auth.hint && <p className="bv-hint mt-1">{found.auth.hint}</p>}
            </div>
          )}

          <details className="mt-5 text-sm">
            <summary className="cursor-pointer text-muted hover:text-ink">How Bevro found this</summary>
            <ul className="mt-2 space-y-0.5 text-muted">
              {found.invocation_label && found.invocation_label !== found.runs_via && <li>Bevro will use: {found.invocation_label}</li>}
              {found.runtimes_found > 1 && !found.choice_needed && <li>{found.runtimes_found} ways to run it were found; the most native one is used.</li>}
              {found.evidence.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </details>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            {!found.invocable && found.needs_bridge ? (
              found.bridge_possible ? (
                <button type="button" className="bv-btn-primary" onClick={makeConnectable} disabled={busy || Boolean(bridge)}>
                  Make it connectable
                </button>
              ) : (
                <Link to="/agents" className="bv-btn-primary">
                  Connect a coding agent
                </Link>
              )
            ) : found.invocable ? (
              <>
                <button type="button" className="bv-btn-primary" onClick={confirm} disabled={busy || (unsure && !name.trim()) || (found.choice_needed && !runtimeId)}>
                  Connect
                </button>
                <button type="button" className="bv-btn" onClick={test} disabled={busy}>
                  {draft?.state === "testing" ? "Testing…" : "Test connection"}
                </button>
              </>
            ) : (
              <Link to="/connect/advanced" className="bv-btn">
                Advanced setup
              </Link>
            )}
            {!found.invocable && found.needs_bridge && (
              <Link to="/connect/advanced" className="bv-link text-sm">
                Advanced setup
              </Link>
            )}
            {testNote && (
              <p className="text-sm" aria-live="polite">
                {testNote}
              </p>
            )}
          </div>

          {bridge && (
            <section aria-label="Preparing connection" className="mt-6 border-t border-line pt-5">
              {bridge.state === "ready" ? (
                <>
                  <p className="flex items-center gap-2 font-medium">
                    <Icon name="check" size={18} />
                    Connected
                  </p>
                  <p className="mt-2 text-muted">{found.name} is available under Agents. Bevro can now route suitable work here.</p>
                  <Link to="/agents" className="bv-btn-primary mt-4">
                    Go to Agents
                  </Link>
                </>
              ) : bridge.state === "failed" ? (
                <>
                  <p role="alert" className="text-sm">
                    {bridge.note}
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-3">
                    <button type="button" className="bv-btn" onClick={makeConnectable} disabled={busy}>
                      Retry
                    </button>
                    <Link to="/connect/advanced" className="bv-link text-sm">
                      Advanced setup
                    </Link>
                  </div>
                </>
              ) : (
                <>
                  <p className="flex items-center gap-2 text-sm text-muted" aria-live="polite">
                    <span className="bv-pulse inline-block h-2 w-2 rounded-full bg-accent" aria-hidden="true" />
                    {bridge.note}
                  </p>
                  <ol className="mt-2 space-y-0.5 text-sm">
                    {bridge.steps.map((step, i) => (
                      <li key={step} className={i === bridge.steps.length - 1 ? "text-ink" : "text-muted"}>
                        {step}
                      </li>
                    ))}
                  </ol>
                  <p className="bv-hint mt-2">This usually takes a few minutes. You can leave this page; it carries on.</p>
                </>
              )}
            </section>
          )}
        </section>
      )}

      {connected && (
        <section aria-label="Connected" className="mt-8 border-t border-line pt-6">
          <p className="flex items-center gap-2 text-lg font-medium">
            <Icon name="check" size={20} />
            Connected
          </p>
          <p className="mt-2 text-muted">
            {connected.name} is available under Agents. Bevro can now route suitable work here.
            {connected.needsWorker && " It will show as available once the worker on this machine confirms it, usually within a minute."}
          </p>
          <div className="mt-4 flex gap-3">
            <Link to="/agents" className="bv-btn-primary">
              Go to Agents
            </Link>
          </div>
        </section>
      )}
    </Page>
  );
}
