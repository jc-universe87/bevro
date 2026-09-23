import { useEffect, useState } from "react";
import Logo from "../components/Logo";
import PageHeader, { Page } from "../components/PageHeader";
import { api, type DeliveryChannelInfo, type Meta, type Workspace } from "../lib/api";
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

  useEffect(() => {
    api.meta().then(setMeta).catch(() => setMeta(null));
    api.listWorkspaces().then(setWorkspaces).catch(() => setWorkspaces([]));
    api.deliveryChannels().then(setChannels).catch(() => setChannels([]));
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

      <section aria-labelledby="projects-heading" className="py-6 border-b border-line">
        <h2 id="projects-heading" className="font-medium mb-1">
          Projects
        </h2>
        <p className="bv-hint mb-3">Where coding agents are allowed to work. Set up on the server.</p>
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
