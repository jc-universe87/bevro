import type { Task } from "../lib/api";
import { statusOf } from "../lib/format";

/**
 * A task's status in a few words, for lists. Words first; the pulsing dot
 * only adds that it is still going. Not a live region: a list of changing
 * rows read aloud would be noise (the task's own page announces changes).
 */
export default function TaskStatus({ task, className = "" }: { task: Task; className?: string }) {
  const status = statusOf(task);
  const active = status.kind === "starting" || status.kind === "working";
  return (
    <span className={`inline-flex items-center gap-2 text-sm ${active ? "text-muted" : "text-ink"} ${className}`}>
      {active && <span className="bv-pulse inline-block h-2 w-2 rounded-pill bg-accent" aria-hidden="true" />}
      {status.label}
    </span>
  );
}
