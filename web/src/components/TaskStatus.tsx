import { isTerminal, type TaskState } from "../lib/api";
import { stateLabel } from "../lib/format";

/** One line of plain wording about where a task is. Neutral colours only. */
export default function TaskStatus({ state, className = "" }: { state: TaskState; className?: string }) {
  const active = !isTerminal(state);
  return (
    <span className={`inline-flex items-center gap-2 text-sm ${active ? "text-muted" : "text-ink"} ${className}`} role="status" aria-live="polite">
      {active && <span className="bv-pulse inline-block h-2 w-2 rounded-pill bg-accent" aria-hidden="true" />}
      {stateLabel(state)}
    </span>
  );
}
