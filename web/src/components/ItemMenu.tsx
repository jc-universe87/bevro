import { useEffect, useId, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { Link } from "react-router-dom";
import type { Provider } from "../lib/api";
import Icon from "./Icon";

/**
 * The quiet "…" on an item in Apps & agents: its page, and taking it out of
 * Bevro. Nothing else - the useful actions are already on the row.
 *
 * A menu in the WAI-ARIA sense: Enter or Space opens it on the first choice,
 * arrows move, Escape closes it and returns to the button, Tab or a tap
 * elsewhere simply closes it.
 */
export function ItemMenu({ provider, onRemove, buttonRef }: { provider: Provider; onRemove: () => void; buttonRef: RefObject<HTMLButtonElement> }) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const choices = () => Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);

  useEffect(() => {
    if (!open) return;
    choices()[0]?.focus();
    // Clear of the phone's bottom navigation (scroll-margin on the menu).
    menuRef.current?.scrollIntoView?.({ block: "nearest" });
    const away = (e: Event) => {
      const target = e.target as Node;
      if (!menuRef.current?.contains(target) && !buttonRef.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open, buttonRef]);

  const onKeyDown = (e: KeyboardEvent) => {
    const all = choices();
    const at = all.indexOf(document.activeElement as HTMLElement);
    const move = (i: number) => {
      e.preventDefault();
      all[(i + all.length) % all.length]?.focus();
    };
    if (e.key === "ArrowDown") move(at + 1);
    else if (e.key === "ArrowUp") move(at - 1);
    else if (e.key === "Home") move(0);
    else if (e.key === "End") move(all.length - 1);
    else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
      buttonRef.current?.focus();
    } else if (e.key === "Tab") setOpen(false);
  };

  const choice = "flex w-full items-center min-h-[44px] px-3 text-left text-sm rounded-md hover:bg-[var(--bv-raised)] focus-visible:bg-[var(--bv-raised)]";

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        className="bv-btn-quiet min-h-[44px] sm:min-h-[44px] min-w-[44px] px-0"
        aria-label={`More options for ${provider.name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="more" />
      </button>
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={provider.name}
          onKeyDown={onKeyDown}
          className="absolute right-0 top-full z-10 mt-1 w-48 max-w-[calc(100vw-2rem)] scroll-mb-24 rounded-lg border bv-sep bg-surface p-1"
          style={{ boxShadow: "var(--bv-shadow-md)" }}
        >
          <Link role="menuitem" tabIndex={-1} to={`/apps/${provider.id}`} className={`${choice} text-ink`}>
            Details
          </Link>
          <div role="separator" className="my-1 border-t bv-sep" />
          <button
            role="menuitem"
            tabIndex={-1}
            type="button"
            className={choice}
            style={{ color: "var(--bv-danger-text)" }}
            onClick={() => {
              setOpen(false);
              onRemove();
            }}
          >
            Remove from Bevro
          </button>
        </div>
      )}
    </div>
  );
}
