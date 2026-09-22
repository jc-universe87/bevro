/** Cmd/Ctrl+K: jump to a page, an agent or a recent task. */
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Provider, type Task } from "../lib/api";
import Icon from "./Icon";
import { NAV } from "./Shell";

interface Item {
  id: string;
  label: string;
  hint?: string;
  to: string;
}

export default function CommandLauncher({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    api.listProviders().then(setProviders).catch(() => setProviders([]));
    api.listTasks().then(setTasks).catch(() => setTasks([]));
    const t = setTimeout(() => inputRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handle = setTimeout(() => {
      api.listTasks({ q: query || undefined }).then(setTasks).catch(() => undefined);
    }, 150);
    return () => clearTimeout(handle);
  }, [query, open]);

  const items = useMemo<Item[]>(() => {
    const q = query.trim().toLowerCase();
    const pages = NAV.filter((n) => !q || n.label.toLowerCase().includes(q)).map((n) => ({ id: `page:${n.to}`, label: n.label, hint: "Go to", to: n.to }));
    const agents = providers
      .filter((p) => !q || p.name.toLowerCase().includes(q) || p.description.toLowerCase().includes(q))
      .map((p) => ({ id: `agent:${p.id}`, label: p.name, hint: p.description, to: "/agents" }));
    const recent = tasks.slice(0, 6).map((t) => ({ id: `task:${t.id}`, label: t.title, hint: t.summary ?? "Recent", to: `/tasks/${t.id}` }));
    return [...pages, ...agents, ...recent].slice(0, 12);
  }, [query, providers, tasks]);

  useEffect(() => setActive(0), [items.length]);

  if (!open) return null;

  const go = (item: Item) => {
    onClose();
    navigate(item.to);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") onClose();
    else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter" && items[active]) {
      e.preventDefault();
      go(items[active]);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center px-4 pt-[12vh]" onKeyDown={onKeyDown}>
      <div className="absolute inset-0 bg-ink/20" onClick={onClose} aria-hidden="true" />
      <div role="dialog" aria-modal="true" aria-label="Search Bevro" className="relative w-full max-w-lg rounded-lg border border-line bg-surface shadow-lg overflow-hidden">
        <div className="flex items-center gap-3 border-b border-line px-4">
          <Icon name="search" className="text-muted shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search pages, agents and recent work"
            aria-label="Search"
            aria-controls="launcher-results"
            className="w-full bg-transparent py-3 text-base outline-none placeholder:text-subtle"
          />
          <button type="button" onClick={onClose} className="bv-btn-quiet -mr-2 min-w-[40px]" aria-label="Close">
            <Icon name="close" />
          </button>
        </div>
        <ul id="launcher-results" role="listbox" aria-label="Results" className="max-h-[50vh] overflow-y-auto py-1">
          {items.length === 0 && <li className="px-4 py-3 text-sm text-muted">Nothing matches.</li>}
          {items.map((item, i) => (
            <li
              key={item.id}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onClick={() => go(item)}
              className={`flex cursor-pointer items-baseline gap-3 px-4 py-2 text-sm ${i === active ? "bg-sunken" : ""}`}
            >
              <span className="text-ink truncate">{item.label}</span>
              {item.hint && <span className="text-muted truncate text-xs ml-auto shrink-0 max-w-[45%]">{item.hint}</span>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
