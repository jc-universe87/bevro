import { useEffect, useState } from "react";
import Logo from "../components/Logo";
import PageHeader, { Page } from "../components/PageHeader";
import { api, type DeliveryChannelInfo, type Meta, type TrustGrant, type Workspace } from "../lib/api";
import { CLEAR_HISTORY_DETAIL, CLEAR_HISTORY_QUESTION } from "./Recent";
import { useTheme, type Theme } from "../lib/theme";

// Where this copy of Bevro came from. One line, so a fork changes it once.
const REPOSITORY = "https://github.com/jc-universe87/bevro";

const THEMES: { value: Theme; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export default function Settings() {
  const [theme, setTheme] = useTheme();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [channels, setChannels] = useState<DeliveryChannelInfo[]>([]);
  const [access, setAccess] = useState<{ grants: TrustGrant[]; ceiling: string[] } | null>(null);
  const [confirm, setConfirm] = useState<"history" | "notifications" | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const run = async (what: "history" | "notifications") => {
    setBusy(true);
    setNote(null);
    try {
      if (what === "history") {
        const { removed } = await api.clearHistory();
        setNote(removed === 0 ? "There was nothing to clear." : `Removed ${removed} ${removed === 1 ? "task" : "tasks"}.`);
      } else {
        const list = await api.listNotifications();
        await Promise.all(list.items.map((n) => api.dismissNotification(n.id)));
        setNote(list.items.length === 0 ? "There was nothing to clear." : `Removed ${list.items.length}.`);
      }
      setConfirm(null);
    } catch {
      setNote("That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    api.meta().then(setMeta).catch(() => setMeta(null));
    api.listWorkspaces().then(setWorkspaces).catch(() => setWorkspaces([]));
    api.deliveryChannels().then(setChannels).catch(() => setChannels([]));
    api.access().then(setAccess).catch(() => setAccess(null));
  }, []);

  return (
    <Page narrow>
      <PageHeader title="Settings" />
      <section aria-labelledby="theme-heading" className="pb-6 border-b border-line">
        <h2 id="theme-heading" className="font-medium mb-3">
          Appearance
        </h2>
        <fieldset>
          <legend className="sr-only">Theme</legend>
          <div className="inline-flex rounded-md border border-line overflow-hidden" role="radiogroup" aria-label="Theme">
            {THEMES.map((t) => (
              <label key={t.value} className={`px-4 py-2 text-sm cursor-pointer min-h-[40px] flex items-center ${theme === t.value ? "bg-sunken font-medium" : "hover:bg-sunken"}`}>
                <input type="radio" name="theme" value={t.value} checked={theme === t.value} onChange={() => setTheme(t.value)} className="sr-only" />
                {t.label}
              </label>
            ))}
          </div>
        </fieldset>
      </section>

      <section aria-labelledby="routing-heading" className="py-6 border-b border-line">
        <h2 id="routing-heading" className="font-medium mb-1">
          Routing
        </h2>
        <p className="text-sm">{meta?.routing?.mode === "llm" ? "Intelligent routing: On" : "Local routing"}</p>
        <p className="bv-hint mt-1">
          {meta?.routing?.mode === "llm"
            ? "Requests and the list of connected agents are sent to the configured routing model to choose who does the work."
            : "Requests are matched to agents by local rules. Nothing leaves this installation to decide."}
        </p>
      </section>

      <section aria-labelledby="notifications-heading" className="py-6 border-b border-line">
        <h2 id="notifications-heading" className="font-medium mb-1">
          Notifications
        </h2>
        <p className="bv-hint mb-3">How Bevro can reach you when a result matters. Set up on the server.</p>
        <ul className="divide-y divide-line border-t border-b border-line">
          {channels.map((channel) => (
            <li key={channel.name} className="py-2.5 flex items-baseline justify-between gap-4">
              <span className="text-sm font-medium">{channel.label}</span>
              <span className="text-sm text-muted text-right">{channel.available ? "Ready" : channel.note ?? "Not set up"}</span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="access-heading" className="py-6 border-b border-line">
        <h2 id="access-heading" className="font-medium mb-1">
          Access &amp; trust
        </h2>
        <p className="bv-hint mb-3">
          What you have allowed Bevro to use on this machine. Taking one back stops it being used from then on; anything it
          already did stays in Recent.
        </p>
        {!access || access.grants.length === 0 ? (
          <p className="bv-hint">Nothing yet. Bevro asks the first time it needs something here.</p>
        ) : (
          <ul className="divide-y divide-line border-t border-b border-line" aria-label="Allowed">
            {access.grants.map((g) => (
              <li key={g.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2.5">
                <div className="min-w-0">
                  <div className="text-sm font-medium">{g.label}</div>
                  <div className="break-all text-xs text-muted">{g.kind === "folder" ? g.target : g.kind === "context" ? `Runs work inside: ${g.target}` : `Program: ${g.target}`}</div>
                  <div className="text-xs text-subtle">
                    {g.kind === "folder" ? (g.scope === "tree" ? "This folder and everything in it" : "This folder") : g.kind === "context" ? "May be used for this project's work" : "May be run"}
                    {g.granted_at ? ` · allowed ${new Date(g.granted_at).toLocaleDateString()}` : ""}
                    {g.granted_by === "migration" ? " · from before Bevro asked" : ""}
                  </div>
                </div>
                <button
                  type="button"
                  className="bv-link text-sm"
                  onClick={async () => {
                    await api.revokeGrant(g.id).catch(() => undefined);
                    setAccess(await api.access().catch(() => access));
                  }}
                >
                  Take back
                </button>
              </li>
            ))}
          </ul>
        )}
        {access && access.ceiling.length > 0 && (
          <p className="bv-hint mt-3">
            This installation is limited to {access.ceiling.length === 1 ? "one folder" : `${access.ceiling.length} folders`} set by whoever
            installed it. Anything allowed here has to be inside {access.ceiling.length === 1 ? "it" : "them"}.
          </p>
        )}
      </section>

      <section aria-labelledby="projects-heading" className="py-6 border-b border-line">
        <h2 id="projects-heading" className="font-medium mb-1">
          Projects
        </h2>
        <p className="bv-hint mb-3">Where coding agents are allowed to work.</p>
        {workspaces.length === 0 ? (
          <p className="bv-hint">None yet.</p>
        ) : (
          <ul className="divide-y divide-line border-t border-b border-line">
            {workspaces.map((w) => (
              <li key={w.id} className="py-2.5">
                <div className="font-medium text-sm">{w.name}</div>
                {w.description && <div className="text-sm text-muted">{w.description}</div>}
                <div className="text-xs text-subtle mt-0.5">{w.permissions.join(" · ")}</div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="data-heading" className="py-6 border-b border-line">
        <h2 id="data-heading" className="font-medium mb-1">
          Workspace data
        </h2>
        <p className="bv-hint mb-3">What Bevro keeps. Your agents, scheduled work and settings are not touched by anything here.</p>
        {confirm === "history" ? (
          <div role="group" aria-label="Clear task history">
            <p className="text-sm">{CLEAR_HISTORY_QUESTION}</p>
            <p className="bv-hint">{CLEAR_HISTORY_DETAIL}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button type="button" className="bv-btn-primary" disabled={busy} onClick={() => void run("history")}>
                Clear task history
              </button>
              <button type="button" className="bv-btn-quiet" onClick={() => setConfirm(null)}>
                Keep it
              </button>
            </div>
          </div>
        ) : confirm === "notifications" ? (
          <div role="group" aria-label="Clear notifications">
            <p className="text-sm">Remove every notification from Bevro?</p>
            <p className="bv-hint">The work they point at stays under Recent. Scheduled work carries on as it is.</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button type="button" className="bv-btn-primary" disabled={busy} onClick={() => void run("notifications")}>
                Clear notifications
              </button>
              <button type="button" className="bv-btn-quiet" onClick={() => setConfirm(null)}>
                Keep them
              </button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <button type="button" className="bv-btn-quiet" onClick={() => setConfirm("history")}>
              Clear task history
            </button>
            <button type="button" className="bv-btn-quiet" onClick={() => setConfirm("notifications")}>
              Clear notifications
            </button>
          </div>
        )}
        {note && (
          <p className="mt-2 text-sm" aria-live="polite">
            {note}
          </p>
        )}
      </section>

      <section aria-labelledby="about-heading" className="pt-6">
        <h2 id="about-heading" className="font-medium mb-3">
          About
        </h2>
        <Logo variant="primary" height={72} />
        <p className="text-sm mt-2">{meta?.tagline ?? "Agents that get things done."}</p>
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm mt-3">
          <dt className="text-muted">Version</dt>
          <dd>{meta?.version ?? "—"}</dd>
          <dt className="text-muted">Server</dt>
          <dd>{meta ? "Connected" : "Not reachable"}</dd>
          <dt className="text-muted">Licence</dt>
          <dd>Apache-2.0</dd>
        </dl>
        <p className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-sm">
          <a className="bv-link" href={REPOSITORY} target="_blank" rel="noreferrer">
            Repository
          </a>
          <a className="bv-link" href={`${REPOSITORY}/tree/main/docs`} target="_blank" rel="noreferrer">
            Documentation
          </a>
          <a className="bv-link" href={`${REPOSITORY}/blob/main/LICENSE`} target="_blank" rel="noreferrer">
            Licence
          </a>
        </p>
        <p className="bv-hint mt-4">
          Bevro is early software. The shape is settled; the details still move.
        </p>
      </section>
    </Page>
  );
}
