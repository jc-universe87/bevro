import { useId, useState, type ReactNode } from "react";

/**
 * A small, optional explanation: "Why?" opens it in place, and nothing on the
 * page depends on it being opened. The button says what it answers, so it
 * reads sensibly on its own to a screen reader, and the answer is announced
 * as part of the button's region when it appears.
 */
export default function Help({ question, children, className = "" }: { question: string; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className={className}>
      <button type="button" className="bv-help" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        <span aria-hidden="true" className="bv-help-mark">
          ?
        </span>
        {question}
      </button>
      <div id={id} role="note" hidden={!open} className="mt-1.5 max-w-prose text-sm text-muted">
        {children}
      </div>
    </div>
  );
}
