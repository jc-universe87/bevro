/** A deliberately small Markdown renderer for provider reports: headings,
 * paragraphs, bullet lists, fenced code and inline code. Everything is built
 * as React elements, never injected as HTML. Anything fancier shows as text. */
import type { ReactNode } from "react";

function inline(text: string, key: number): ReactNode {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean);
  return (
    <span key={key}>
      {parts.map((part, i) => {
        if (part.startsWith("`") && part.endsWith("`")) return <code key={i} className="rounded-sm bg-sunken px-1 text-[0.9em]">{part.slice(1, -1)}</code>;
        if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
        return part;
      })}
    </span>
  );
}

export default function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++;
      out.push(<pre key={key++} className="my-2 overflow-x-auto rounded-md border border-line bg-sunken p-3 text-xs leading-relaxed">{code.join("\n")}</pre>);
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const cls = level <= 2 ? "mt-4 mb-1 text-base font-semibold" : "mt-3 mb-1 text-sm font-semibold";
      out.push(<p key={key++} className={cls} role="heading" aria-level={Math.min(level + 2, 6)}>{inline(heading[2], 0)}</p>);
      i++;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*]\s+/, ""));
      out.push(<ul key={key++} className="my-1 list-disc pl-5 space-y-0.5">{items.map((it, j) => <li key={j}>{inline(it, j)}</li>)}</ul>);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ""));
      out.push(<ol key={key++} className="my-1 list-decimal pl-5 space-y-0.5">{items.map((it, j) => <li key={j}>{inline(it, j)}</li>)}</ol>);
      continue;
    }
    if (line.trim() === "") {
      i++;
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() !== "" && !/^(#{1,4}\s|```|\s*[-*]\s+|\s*\d+[.)]\s+)/.test(lines[i])) para.push(lines[i++]);
    out.push(<p key={key++} className="my-1.5">{inline(para.join(" "), 0)}</p>);
  }
  return <div className="text-sm leading-relaxed">{out}</div>;
}
