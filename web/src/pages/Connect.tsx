import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import Help from "../components/Help";
import Icon from "../components/Icon";
import PageHeader, { Page } from "../components/PageHeader";
import TestResults from "../components/TestResults";
import { api, ApiError, type BridgeStatus, type ConnectDraft, type DraftView } from "../lib/api";
import { connectStep, type ActionId, type Step } from "../lib/connectSteps";

const POLL_MS = 800;

function reason(err: unknown): string {
  return err instanceof ApiError ? err.message : "Bevro couldn't reach the server.";
}

/** How it runs, in words, for "How Bevro found this". */
function howItRuns(draft: DraftView): string | null {
  if (!draft.runs_via) return null;
  const where = draft.runs_at ? ` · ${draft.runs_at.toLowerCase()}` : "";
  return `${draft.runs_via}${where}`;
}

export default function Connect() {
  const navigate = useNavigate();
  const [target, setTarget] = useState("");
  // What was last looked for: "Try again" looks for it again.
  const [lookedFor, setLookedFor] = useState("");
  const [draft, setDraft] = useState<ConnectDraft | null>(null);
  const [connected, setConnected] = useState<{ id: string; name: string; needsWorker: boolean; needsCredential: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // What the person typed along the way. Kept across steps: nothing typed is lost.
  const [summary, setSummary] = useState("");
  const [secret, setSecret] = useState("");
  const [credentialAdded, setCredentialAdded] = useState(false);
  const [runtimeId, setRuntimeId] = useState<string | null>(null);
  const [scope, setScope] = useState<string | null>(null);
  const [bridge, setBridge] = useState<BridgeStatus | null>(null);
  const pollRef = useRef<number | null>(null);
  const bridgeRef = useRef<number | null>(null);
  const targetRef = useRef<HTMLInputElement>(null);
  const stepRef = useRef<HTMLHeadingElement>(null);
  const inputId = useId();

  const stopPolling = () => {
    for (const ref of [pollRef, bridgeRef]) {
      if (ref.current !== null) {
        window.clearTimeout(ref.current);
        ref.current = null;
      }
    }
  };
  useEffect(() => stopPolling, []);

  const step: Step | null = connected ? null : connectStep(draft, { credentialAdded, bridge });

  // A new step is announced and focused, so the next thing is where attention is.
  const lastKind = useRef<string | null>(null);
  useEffect(() => {
    if (step && !step.busy && step.kind !== lastKind.current) {
      stepRef.current?.focus({ preventScroll: false });
    }
    lastKind.current = step?.kind ?? null;
  }, [step?.kind, step?.busy]);

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

  const adopt = (d: ConnectDraft) => {
    setDraft(d);
    if (d.draft) {
      setRuntimeId((current) => current ?? (d.draft!.choice_needed ? null : d.draft!.runtime?.id ?? null));
    }
    if (d.state === "looking" || d.state === "testing") {
      pollRef.current = window.setTimeout(async () => {
        try {
          adopt(await api.connectDraft(d.id));
        } catch {
          setError("Bevro couldn't reach the server.");
          setBusy(false);
        }
      }, POLL_MS);
    } else {
      setBusy(false);
    }
  };

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(reason(err));
      setBusy(false);
    }
  };

  const reset = () => {
    stopPolling();
    setDraft(null);
    setConnected(null);
    setSummary("");
    setSecret("");
    setCredentialAdded(false);
    setRuntimeId(null);
    setScope(null);
    setBridge(null);
    setError(null);
  };

  const lookFor = (text: string) => {
    reset();
    setLookedFor(text);
    return run(async () => adopt(await api.connectDiscover(text)));
  };

  const discover = (e: FormEvent) => {
    e.preventDefault();
    const text = target.trim();
    if (!text || busy) return;
    void lookFor(text);
  };

  const startOver = () => {
    reset();
    setTarget("");
    setLookedFor("");
    targetRef.current?.focus();
  };

  const secrets = () => (draft?.draft?.auth.secret_name && secret.trim() ? { [draft.draft.auth.secret_name]: secret.trim() } : {});

  const confirm = (withCredential = true) =>
    run(async () => {
      if (!draft?.draft) return;
      const found = draft.draft;
      const body: Parameters<typeof api.connectConfirm>[1] = { secrets: withCredential ? secrets() : {} };
      if (found.choice_needed && runtimeId) body.runtime_id = runtimeId;
      if (scope) body.scope = scope;
      const provider = await api.connectConfirm(draft.id, body);
      stopPolling();
      setConnected({
        id: provider.id,
        name: provider.name,
        needsWorker: found.availability === "needs_worker",
        needsCredential: found.auth.required && !(withCredential && secret.trim()),
      });
      setDraft(null);
      setTarget("");
      setBusy(false);
    });

  const describe = (e?: FormEvent) => {
    e?.preventDefault();
    if (!draft || !summary.trim()) return;
    void run(async () => adopt(await api.connectDescribe(draft.id, summary.trim())));
  };

  const addCredential = (e?: FormEvent) => {
    e?.preventDefault();
    if (!secret.trim()) return;
    setCredentialAdded(true);
  };

  const act = (id: ActionId) => {
    if (!draft && id !== "open") return;
    switch (id) {
      case "connect":
        return void confirm();
      case "connect_without_credential":
        setSecret("");
        return void confirm(false);
      case "describe":
        return describe();
      case "add_credential":
        return addCredential();
      case "test":
      case "check_again":
        return void run(async () => adopt(await api.connectTest(draft!.id, secrets())));
      case "allow":
        return void run(async () => adopt(await api.connectAllow(draft!.id, "exact")));
      case "build":
        return void run(async () => followBridge(await api.connectBridge(draft!.id)));
      case "setup":
        return navigate(`/connect/advanced?from=${encodeURIComponent(draft!.id)}`);
      case "retry":
        return void lookFor(lookedFor || target.trim());
      case "try_another":
        targetRef.current?.focus();
        targetRef.current?.select();
        return;
      case "open":
        return navigate(draft?.already_connected ? `/agents?manage=${draft.already_connected.id}` : "/agents");
      case "choose":
        return; // each match has its own button
    }
  };

  const found = draft?.state === "found" || draft?.state === "testing" ? draft.draft : null;
  // Nothing new to look for while the field still says what was looked for.
  const showFind = !draft || target.trim() !== lookedFor || draft.state === "failed";
  const primaryDisabled =
    busy ||
    (step?.input === "description" && !summary.trim()) ||
    (step?.input === "credential" && !secret.trim()) ||
    (step?.kind === "needs_choice" && ((found?.scope_choices ?? []).length > 0 ? !scope : !runtimeId));

  const helpId = `${inputId}-hint`;
  const messageId = `${inputId}-message`;

  const primaryButton = (s: Step, submit = false) =>
    s.primary && (
      <button type={submit ? "submit" : "button"} className="bv-btn-primary" onClick={submit ? undefined : () => act(s.primary!.id)} disabled={primaryDisabled}>
        {busy && (s.primary.id === "describe" || s.primary.id === "connect") ? (s.primary.id === "describe" ? "Saving…" : "Connecting…") : s.primary.label}
      </button>
    );

  const quietActions = (s: Step) => (
    <>
      {s.secondary && (
        <button type="button" className="bv-btn" onClick={() => act(s.secondary!.id)} disabled={busy}>
          {s.secondary.id === "test" && draft?.state === "testing" ? "Testing…" : s.secondary.label}
        </button>
      )}
      {(s.tertiary ?? []).map((a) => (
        <button key={a.id} type="button" className="bv-link text-sm" onClick={() => act(a.id)} disabled={busy}>
          {a.label}
        </button>
      ))}
    </>
  );

  /** The one thing to do next, with whatever it needs typed or picked. */
  const stepPanel = (s: Step) => (
    <section aria-label="Next step" className={found ? "mt-6 rounded-md border border-line p-4" : "mt-6 border-t border-line pt-5"}>
      {s.input === "description" ? (
        <form onSubmit={describe}>
          <label htmlFor={`${inputId}-summary`} className="font-medium block">
            <span ref={stepRef as React.Ref<HTMLSpanElement>} tabIndex={-1} className="outline-none">
              {s.heading}
            </span>
          </label>
          <input
            id={`${inputId}-summary`}
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="e.g. search documents, create reports"
            className="bv-input mt-2"
            aria-describedby={helpId}
            autoComplete="off"
          />
          <p id={helpId} className="bv-hint mt-1">
            Bevro uses this to know when it can help.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            {primaryButton(s, true)}
            {quietActions(s)}
          </div>
        </form>
      ) : s.input === "credential" && found ? (
        <form onSubmit={addCredential}>
          <h2 ref={stepRef} tabIndex={-1} className="font-medium outline-none">
            {s.heading}
          </h2>
          {s.message && (
            <p id={messageId} className="mt-1 text-sm text-muted">
              {s.message}
            </p>
          )}
          <label htmlFor={`${inputId}-secret`} className="bv-label mt-3">
            {found.auth.label ?? "Credential"}
          </label>
          <input
            id={`${inputId}-secret`}
            type="password"
            autoComplete="off"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            className="bv-input max-w-sm"
            aria-describedby={`${s.message ? messageId : ""} ${helpId}`.trim()}
          />
          <p id={helpId} className="bv-hint mt-1">
            Stored encrypted and never shown again.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            {primaryButton(s, true)}
            {quietActions(s)}
          </div>
        </form>
      ) : (
        <>
          <h2 ref={stepRef} tabIndex={-1} className="font-medium outline-none">
            {s.heading}
          </h2>
          {s.message && <p className="mt-1 text-sm text-muted">{s.message}</p>}

          {s.kind === "trust_required" && draft?.trust?.kind === "folder" && draft.trust.path && <p className="mt-2 break-all font-mono text-sm">{draft.trust.path}</p>}

          {s.input === "list" && draft?.choices && (
            <ul className="mt-3 divide-y divide-line border-t border-b border-line">
              {draft.choices.map((c, i) => (
                <li key={c.where} className="flex flex-wrap items-baseline justify-between gap-2 py-2.5">
                  <div className="min-w-0">
                    <div className="text-sm font-medium">{c.label}</div>
                    <div className="break-all font-mono text-xs text-muted">{c.where}</div>
                  </div>
                  <button type="button" className="bv-btn" aria-label={`Choose ${c.label} (${c.where})`} onClick={() => void run(async () => adopt(await api.connectChoose(draft.id, i)))} disabled={busy}>
                    Choose
                  </button>
                </li>
              ))}
            </ul>
          )}

          {s.input === "choice" && found && (
            <fieldset className="mt-3">
              <legend className="sr-only">{s.heading}</legend>
              <div className="space-y-1">
                {(found.scope_choices ?? []).length > 0
                  ? found.scope_choices!.map((c) => (
                      <label key={c.value} className="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="radio" name="scope" value={c.value} checked={scope === c.value} onChange={() => setScope(c.value)} className="accent-[var(--bv-accent)]" />
                        {c.label}
                      </label>
                    ))
                  : found.runtime_options.map((o) => (
                      <label key={o.id} className="flex items-start gap-2 text-sm cursor-pointer">
                        <input type="radio" name="runtime" value={o.id} checked={runtimeId === o.id} onChange={() => setRuntimeId(o.id)} className="mt-1 accent-[var(--bv-accent)]" />
                        <span>
                          {o.display_name}
                          <span className="text-subtle"> · {o.credentials.label === "Missing" ? "needs a credential" : o.credentials.label.toLowerCase()}</span>
                        </span>
                      </label>
                    ))}
              </div>
              {primaryDisabled && !busy && <p className="bv-hint mt-2">Choose one to continue.</p>}
            </fieldset>
          )}

          {s.busy && (
            <p className="mt-2 flex items-center gap-2 text-sm text-muted" aria-live="polite">
              <span className="bv-pulse inline-block h-2 w-2 rounded-full bg-accent" aria-hidden="true" />
              {s.kind === "building" && bridge ? bridge.steps[bridge.steps.length - 1] ?? "Working…" : "Working…"}
            </p>
          )}
          {s.kind === "building" && bridge && bridge.steps.length > 1 && (
            <ol className="mt-2 space-y-0.5 text-sm text-muted">
              {bridge.steps.slice(0, -1).map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ol>
          )}

          {(s.primary || s.secondary || s.tertiary) && s.input !== "list" && (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              {primaryButton(s)}
              {quietActions(s)}
              {s.kind === "trust_required" && (
                <>
                  <button type="button" className="bv-link text-sm" onClick={startOver} disabled={busy}>
                    Cancel
                  </button>
                  {draft?.trust?.kind === "folder" && draft.trust.parent_label && (
                    <button type="button" className="bv-link text-sm" onClick={() => void run(async () => adopt(await api.connectAllow(draft.id, "parent")))} disabled={busy}>
                      Allow everything in {draft.trust.parent_label} instead
                    </button>
                  )}
                </>
              )}
            </div>
          )}
          {s.input === "list" && (
            <button type="button" className="bv-link mt-3 text-sm" onClick={startOver} disabled={busy}>
              Cancel
            </button>
          )}
        </>
      )}

      {s.help && (
        <Help question={s.help.question} className="mt-3">
          {s.help.answer}
        </Help>
      )}

      {draft?.test && found && s.kind !== "testing" && <TestResults result={draft.test} />}
    </section>
  );

  return (
    <Page narrow>
      <PageHeader title="Connect" />
      <form onSubmit={discover} aria-label="Connect">
        <label htmlFor="connect-target" className="text-lg font-medium block mb-3">
          Connect an agent, app or service to Bevro.
        </label>
        <input
          id="connect-target"
          ref={targetRef}
          autoFocus
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="Paste an address, folder, command, or name"
          className="bv-input py-3"
          autoComplete="off"
          spellCheck={false}
          disabled={busy && draft?.state === "looking"}
        />
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
          {showFind && (
            <button type="submit" className={draft && draft.state !== "failed" ? "bv-btn" : "bv-btn-primary"} disabled={busy || !target.trim()}>
              {busy && draft?.state === "looking" ? "Looking…" : "Connect"}
            </button>
          )}
          {!showFind && (
            <button type="button" className="bv-link text-sm" onClick={startOver} disabled={busy && draft?.state === "looking"}>
              Start over
            </button>
          )}
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

      {step && !found && stepPanel(step)}

      {found && step && (
        <section aria-label="Found" className="mt-8 border-t border-line pt-6">
          <p className="text-xs uppercase tracking-wide text-subtle mb-2">{draft?.found_by_name ? "Found on this machine" : "Found"}</p>
          <h2 className="text-xl font-semibold tracking-tight">{found.name}</h2>
          {found.description && <p className="mt-1 text-muted">{found.description}</p>}

          {/* Once described, the sentence above is made of these same words. */}
          {!found.needs_description && !found.described && found.capabilities.length > 0 && (
            <div className="mt-4">
              <p className="text-sm text-muted mb-1">Can:</p>
              <ul className="space-y-0.5" aria-label="Capabilities">
                {found.capabilities.map((c) => (
                  <li key={c.id} className="text-sm">
                    • {c.title ?? c.id}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {found.connected_for && !(found.scope_choices ?? []).length && (
            <p className="mt-4 text-sm">
              <span className="text-muted">Connected for:</span> {found.connected_for}
            </p>
          )}

          {!found.auth.required && found.auth.hint && (
            <p className="mt-4 text-sm">
              <span className="text-muted">Credentials:</span> {found.auth.hint}
            </p>
          )}

          {stepPanel(step)}

          <details className="mt-5 text-sm">
            <summary className="cursor-pointer text-muted hover:text-ink">How Bevro found this</summary>
            <ul className="mt-2 space-y-0.5 text-muted">
              {howItRuns(found) && <li>Runs via: {howItRuns(found)}</li>}
              {found.invocation_label && found.invocation_label !== found.runs_via && <li>Bevro will use: {found.invocation_label}</li>}
              <li>Confidence: {found.confidence_label}</li>
              {found.runtimes_found > 1 && !found.choice_needed && <li>{found.runtimes_found} ways to run it were found; the most native one is used.</li>}
              {found.evidence.map((e) => (
                <li key={e}>{e}</li>
              ))}
              {found.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
            {found.source_description && (
              <div className="mt-3">
                <p className="text-muted">As it describes itself</p>
                <p className="mt-1 whitespace-pre-line text-subtle">{found.source_description}</p>
              </div>
            )}
          </details>
        </section>
      )}

      {connected && (
        <section aria-label="Connected" className="mt-8 border-t border-line pt-6">
          <p className="flex items-center gap-2 text-lg font-medium">
            <Icon name="check" size={20} />
            Connected
          </p>
          {connected.needsCredential ? (
            <>
              <p className="mt-2 text-muted">{connected.name} is one of your agents now, but it needs a credential before it can run.</p>
              <div className="mt-4 flex gap-3">
                <Link to={`/agents?manage=${connected.id}&credential=1`} className="bv-btn-primary">
                  Add credential
                </Link>
              </div>
            </>
          ) : (
            <>
              <p className="mt-2 text-muted">
                {connected.name} is available under Agents. Bevro can now route suitable work here.
                {connected.needsWorker && " It will show as available once the worker on this machine confirms it, usually within a minute."}
              </p>
              <div className="mt-4 flex gap-3">
                <Link to="/agents" className="bv-btn-primary">
                  Go to Agents
                </Link>
              </div>
            </>
          )}
        </section>
      )}
    </Page>
  );
}
