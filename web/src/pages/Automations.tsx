import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import PageHeader, { Page } from "../components/PageHeader";
import { api, ApiError, type Automation, type DeliveryChannelInfo, type NotifyPreference } from "../lib/api";

const TIMEZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

function nextWords(automation: Automation): string {
  if (!automation.enabled) return "";  // the badge beside the title says so
  if (!automation.next_run_at) return "Not scheduled";
  const next = new Date(automation.next_run_at);
  const days = Math.round((next.getTime() - Date.now()) / 86_400_000);
  if (days <= 0) return `Next: today, ${next.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  if (days === 1) return `Next: tomorrow, ${next.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  if (days < 7) return `Next: ${next.toLocaleDateString([], { weekday: "long" })}`;
  return `Next: ${next.toLocaleDateString([], { day: "numeric", month: "short" })}`;
}

/** "When this needs my attention". Deliberately three lines, not a preference centre. */
function NotifySection({
  automation,
  channels,
  value,
  onChange,
  disabled,
}: {
  automation: Automation;
  channels: DeliveryChannelInfo[];
  value: NotifyPreference;
  onChange: (next: NotifyPreference) => void;
  disabled: boolean;
}) {
  // A channel this installation cannot use at all is not offered.
  const offered = channels.filter((c) => c.offerable || c.name === "in_app");
  const destinationKey = (name: string) => (name === "email" ? "email_to" : "webhook_url") as "email_to" | "webhook_url";
  return (
    <fieldset className="mt-3">
      <legend className="text-sm font-medium">When this needs my attention</legend>
      <div className="mt-1 flex flex-col gap-1">
        {offered.map((channel) => {
          const on = Boolean(value[channel.name as keyof NotifyPreference]);
          const key = destinationKey(channel.name);
          return (
            <div key={channel.name}>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={on}
                  disabled={disabled}
                  onChange={(e) => onChange({ ...value, [channel.name]: e.target.checked })}
                />
                {channel.label}
              </label>
              {/* Only once it is ticked, and only where an address makes sense. */}
              {on && channel.accepts_destination && (
                <div className="mt-1 ml-6">
                  <label htmlFor={`${channel.name}-to-${automation.id}`} className="sr-only">
                    {channel.name === "email" ? "Email address" : "Web address"}
                  </label>
                  <input
                    id={`${channel.name}-to-${automation.id}`}
                    value={value[key] ?? ""}
                    disabled={disabled}
                    onChange={(e) => onChange({ ...value, [key]: e.target.value })}
                    placeholder={channel.needs_destination
                      ? (channel.name === "email" ? "you@example.com" : "https://example.com/hook")
                      : "Somewhere else (optional)"}
                    className="bv-input text-sm"
                  />
                </div>
              )}
            </div>
          );
        })}
        {automation.mode !== "monitoring" && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={value.on_finish} disabled={disabled} onChange={(e) => onChange({ ...value, on_finish: e.target.checked })} />
            Tell me each time it finishes
          </label>
        )}
      </div>
      <p className="bv-hint mt-1">
        {automation.mode === "monitoring"
          ? "Bevro stays quiet unless the result is worth telling you about."
          : "Results always appear under Recent."}
      </p>
    </fieldset>
  );
}

const IN_BEVRO_ONLY: NotifyPreference = { in_app: true, email: false, webhook: false, on_finish: false, email_to: "", webhook_url: "" };

/** Only said when it is more than the default: silence means "in Bevro". */
function notifyWords(automation: Automation): string {
  const notify = automation.notify ?? IN_BEVRO_ONLY;
  const extra = [notify.email && "email", notify.webhook && "a webhook"].filter(Boolean) as string[];
  if (extra.length === 0) return "";
  return `Also by ${extra.join(" and ")}`;
}

function Row({ automation, channels, onChange, onRemoved }: { automation: Automation; channels: DeliveryChannelInfo[]; onChange: (a: Automation) => void; onRemoved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [when, setWhen] = useState("");
  const [notify, setNotify] = useState<NotifyPreference>(automation.notify ?? IN_BEVRO_ONLY);
  const [confirmRemove, setConfirmRemove] = useState(false);

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

  const toggle = () => run(async () => onChange(await api.updateAutomation(automation.id, { enabled: !automation.enabled, timezone: TIMEZONE })));
  const runNow = () =>
    run(async () => {
      onChange(await api.runAutomation(automation.id));
      setNote("Started. It will appear under Recent.");
    });
  const save = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      onChange(
        await api.updateAutomation(automation.id, {
          ...(when.trim() ? { when: when.trim() } : {}),
          notify,
          timezone: TIMEZONE,
        }),
      );
      setEditing(false);
      setWhen("");
    });
  };

  return (
    <li className="py-4">
      <div className="flex flex-wrap items-baseline gap-x-2 min-w-0">
        <h2 className="font-medium min-w-0 break-words">{automation.title}</h2>
        {!automation.enabled && <span className="text-xs text-subtle">Paused</span>}
      </div>
      <p className="text-sm text-muted">
        {automation.schedule}
        {automation.condition && <> · {automation.condition}</>}
        {automation.provider && <> · {automation.provider.name}</>}
      </p>
      <p className="text-sm text-subtle empty:hidden">
        {[automation.last_result, nextWords(automation), notifyWords(automation)].filter(Boolean).join(" · ")}
      </p>

      {editing ? (
        <form onSubmit={save} className="mt-3">
          <div className="flex flex-col sm:flex-row gap-2">
            <label htmlFor={`when-${automation.id}`} className="sr-only">
              When should Bevro run this?
            </label>
            <input id={`when-${automation.id}`} autoFocus value={when} onChange={(e) => setWhen(e.target.value)} placeholder={automation.schedule} className="bv-input" disabled={busy} />
          </div>
          <NotifySection automation={automation} channels={channels} value={notify} onChange={setNotify} disabled={busy} />
          <div className="mt-3 flex gap-2">
            <button type="submit" className="bv-btn-primary" disabled={busy}>
              Save
            </button>
            <button type="button" className="bv-btn-quiet" onClick={() => { setEditing(false); setNotify(automation.notify ?? IN_BEVRO_ONLY); }}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-1 text-sm">
          <button type="button" className="bv-link py-1" onClick={toggle} disabled={busy}>
            {automation.enabled ? "Pause" : "Resume"}
          </button>
          <span className="text-subtle" aria-hidden="true">·</span>
          <button type="button" className="bv-link py-1" onClick={runNow} disabled={busy}>
            Run now
          </button>
          <span className="text-subtle" aria-hidden="true">·</span>
          <button type="button" className="bv-link py-1" onClick={() => setEditing(true)}>
            Edit
          </button>
          <span className="text-subtle" aria-hidden="true">·</span>
          {confirmRemove ? (
            <>
              <button type="button" className="bv-link py-1" onClick={() => void run(async () => { await api.removeAutomation(automation.id); onRemoved(); })} disabled={busy}>
                Yes, remove
              </button>
              <button type="button" className="text-muted hover:text-ink py-1 ml-2" onClick={() => setConfirmRemove(false)}>
                Keep
              </button>
            </>
          ) : (
            <button type="button" className="text-muted hover:text-ink py-1" onClick={() => setConfirmRemove(true)}>
              Remove
            </button>
          )}
        </div>
      )}
      {note && (
        <p className="mt-1 text-sm" aria-live="polite">
          {note}
        </p>
      )}
    </li>
  );
}

export default function Automations() {
  const [automations, setAutomations] = useState<Automation[] | null>(null);
  const [channels, setChannels] = useState<DeliveryChannelInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listAutomations().then(setAutomations).catch(() => setError("Scheduled work couldn't be loaded."));
    // What this installation can actually do. Email appears only when set up.
    api.deliveryChannels().then(setChannels).catch(() => setChannels([]));
  }, []);

  const replace = (updated: Automation) => setAutomations((list) => (list ?? []).map((a) => (a.id === updated.id ? updated : a)));
  const drop = (id: string) => setAutomations((list) => (list ?? []).filter((a) => a.id !== id));

  return (
    <Page>
      <PageHeader title="Scheduled" />
      {error && <p role="alert">{error}</p>}
      {automations && automations.length === 0 && (
        <p className="bv-hint py-8">
          Nothing is scheduled yet. Finish a task and choose <Link to="/recent" className="bv-link">Schedule</Link>, or say when on Home — “every Monday morning”.
        </p>
      )}
      <ul className="bv-divide empty:hidden" aria-label="Scheduled work">
        {(automations ?? []).map((automation) => (
          <Row key={automation.id} automation={automation} channels={channels} onChange={replace} onRemoved={() => drop(automation.id)} />
        ))}
      </ul>
    </Page>
  );
}
