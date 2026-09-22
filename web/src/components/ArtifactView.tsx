/** Progressive rendering of a result.
 *
 * Bevro renders the types it understands. Anything else - a type it has never
 * seen, or a known type without usable content - falls back to a plain
 * "Open result" link when there is somewhere to open. */
import type { Artifact } from "../lib/api";
import Icon from "./Icon";
import Markdown from "./Markdown";

function payloadText(a: Artifact): string | null {
  const p = a.payload as Record<string, unknown> | null;
  if (p && typeof p.text === "string") return p.text;
  return null;
}

function StructuredTable({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  return (
    <table className="text-sm min-w-[18rem]">
      <thead>
        <tr className="text-left text-muted">
          {columns.map((c, j) => (
            <th key={c} className={`font-medium py-1 border-b border-line ${j > 0 ? "text-right pl-8" : "pr-8"}`}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => (
              <td key={j} className={`py-1.5 border-b border-line ${j > 0 ? "text-right pl-8 tabular-nums" : "pr-8"}`}>
                {String(cell)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function OpenLink({ href, label }: { href: string; label: string }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="bv-link inline-flex items-center gap-1 text-sm font-medium">
      {label}
      <Icon name="external" size={16} />
    </a>
  );
}

export default function ArtifactView({ artifact }: { artifact: Artifact }) {
  const a = artifact;
  const open = a.external_url ?? a.content_url;

  // Deep links are the headline action for a specialist provider.
  if (a.type === "deep_link" && a.external_url) {
    return (
      <div>
        <a href={a.external_url} target="_blank" rel="noopener noreferrer" className="bv-link inline-flex items-center gap-1 text-base font-medium">
          {a.title} →
        </a>
        {a.summary && <p className="bv-hint mt-0.5">{a.summary}</p>}
      </div>
    );
  }

  const text = payloadText(a);
  if ((a.type === "text" || a.type === "note" || a.type === "report") && text) {
    const markdown = a.mime_type === "text/markdown";
    return (
      <div>
        <h3 className="text-sm font-medium">{a.title}</h3>
        {markdown ? (
          <div className="mt-1">
            <Markdown text={text} />
          </div>
        ) : (
          <pre className="mt-2 whitespace-pre-wrap font-[inherit] text-sm text-ink leading-relaxed">{text}</pre>
        )}
        {a.content_url && (
          <div className="mt-2">
            <OpenLink href={a.content_url} label="Open file" />
          </div>
        )}
      </div>
    );
  }

  if (a.type === "diff" && text) {
    return (
      <details className="group">
        <summary className="cursor-pointer text-sm font-medium text-accent hover:text-accent-hover list-none inline-flex items-center gap-1">
          <span className="inline-block transition-transform group-open:rotate-90" aria-hidden="true">›</span>
          {a.title}
        </summary>
        <pre className="mt-2 max-h-[28rem] overflow-auto rounded-md border border-line bg-sunken p-3 text-xs leading-relaxed">{text}</pre>
      </details>
    );
  }

  const p = a.payload as { columns?: unknown; rows?: unknown } | null;
  if (a.type === "structured" && p && Array.isArray(p.columns) && Array.isArray(p.rows)) {
    return (
      <div>
        <h3 className="text-sm font-medium mb-2">{a.title}</h3>
        <StructuredTable columns={p.columns as string[]} rows={p.rows as unknown[][]} />
      </div>
    );
  }

  if (a.type === "image" && (a.content_url || a.external_url)) {
    return (
      <figure>
        <img src={a.content_url ?? a.external_url ?? ""} alt={a.summary ?? a.title} className="max-w-full rounded-md border border-line" />
        <figcaption className="bv-hint mt-1">{a.title}</figcaption>
      </figure>
    );
  }

  if (a.type === "file" && a.content_url) {
    return (
      <div>
        <h3 className="text-sm font-medium">{a.title}</h3>
        {a.summary && <p className="bv-hint">{a.summary}</p>}
        <OpenLink href={a.content_url} label="Download" />
      </div>
    );
  }

  // Unknown type, or a known type Bevro cannot render from what it was given.
  return (
    <div>
      <h3 className="text-sm font-medium">{a.title}</h3>
      {a.summary && <p className="bv-hint">{a.summary}</p>}
      {open ? <OpenLink href={open} label="Open result" /> : <p className="bv-hint">This result can't be shown here yet.</p>}
    </div>
  );
}
