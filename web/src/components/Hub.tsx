import { useState, type FormEvent, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, type Provider } from "../lib/api";
import { openingHelp, type Opening } from "../lib/hub";
import Icon from "./Icon";

/** The letter an app or agent is recognised by. Decoration: its name is always written next to it. */
export function LetterMark({ provider, size = "md" }: { provider: Provider; size?: "md" | "lg" }) {
  const letter = provider.icon?.text ?? provider.name.slice(0, 1).toUpperCase();
  const box = size === "lg" ? "h-12 w-12 text-lg rounded-lg" : "h-10 w-10 text-base rounded-md";
  return (
    <span aria-hidden="true" className={`flex shrink-0 items-center justify-center font-semibold text-accent ${box}`} style={{ background: "var(--bv-accent-subtle)" }}>
      {letter}
    </span>
  );
}

export type Tone = "positive" | "neutral" | "attention";

/** A status is a shape and words, never a colour alone. */
export function StatusText({ tone, children }: { tone: Tone; children: ReactNode }) {
  const dot = tone === "attention" ? "bv-dot bv-dot-attention" : tone === "neutral" ? "bv-dot bv-dot-neutral" : "bv-dot";
  return (
    <span className="bv-status">
      <span className={dot} aria-hidden="true" />
      <span className={tone === "attention" ? "text-ink" : undefined}>{children}</span>
    </span>
  );
}

/** Give a task to something Bevro can send work to. */
export function AskForm({ provider, onDone }: { provider: Provider; onDone: () => void }) {
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
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server. Try again in a moment.");
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="mt-3 flex flex-col gap-2 sm:flex-row">
      <label htmlFor={`ask-${provider.id}`} className="sr-only">
        What should {provider.name} do?
      </label>
      <input
        id={`ask-${provider.id}`}
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={`What should ${provider.name} do?`}
        className="bv-input"
        disabled={busy}
      />
      <div className="flex gap-2">
        <button type="submit" className="bv-btn-primary" disabled={busy || !text.trim()}>
          {busy ? "Sending…" : "Send"}
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

/** An external link that says it leaves Bevro. */
export function OpenLink({ href, label, primary = false, className = "" }: { href: string; label: string; primary?: boolean; className?: string }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={`${primary ? "bv-btn-primary" : "bv-btn"} ${className}`}>
      {label}
      <Icon name="external" size={15} />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}

/**
 * Where to open an app when this browser can't follow a link to it, and a
 * way to tell Bevro the address that does work from here.
 */
export function OpeningHelp({ provider, opening, onChange }: { provider: Provider; opening: Opening | null; onChange: (p: Provider) => void }) {
  const help = openingHelp(provider.name, opening);
  const [editing, setEditing] = useState(false);
  return (
    <div>
      {help && <p className="text-sm text-muted max-w-prose">{help}</p>}
      {editing ? (
        <AddressForm provider={provider} onSaved={(p) => { onChange(p); setEditing(false); }} onCancel={() => setEditing(false)} />
      ) : (
        <button type="button" className="bv-link mt-1 text-sm" onClick={() => setEditing(true)}>
          {help ? "Give Bevro the address you use" : "Use a different address"}
        </button>
      )}
    </div>
  );
}

function AddressForm({ provider, onSaved, onCancel }: { provider: Provider; onSaved: (p: Provider) => void; onCancel: () => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = `address-${provider.id}`;
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.updateProvider(provider.id, { web_address: value.trim() }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't save that. Try again in a moment.");
      setBusy(false);
    }
  };
  return (
    <form onSubmit={save} className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-start">
      <div className="flex-1">
        <label htmlFor={id} className="bv-label">
          Address of {provider.name}
        </label>
        <input id={id} type="url" inputMode="url" autoFocus value={value} onChange={(e) => setValue(e.target.value)} placeholder="https://" className="bv-input" disabled={busy} aria-describedby={`${id}-hint`} />
        <p id={`${id}-hint`} className="bv-hint mt-1">
          The address you open it at on this device. Bevro only links to it.
        </p>
        {error && (
          <p role="alert" className="mt-1 text-sm">
            {error}
          </p>
        )}
      </div>
      <div className="flex gap-2 sm:mt-6">
        <button type="submit" className="bv-btn-primary" disabled={busy || !/^https?:\/\/\S+/i.test(value.trim())}>
          Save
        </button>
        <button type="button" className="bv-btn-quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
