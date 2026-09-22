import type { TaskState } from "./api";

/** Human wording for a task state. Neutral and textual by design. */
export const STATE_LABEL: Record<TaskState, string> = {
  created: "Created",
  queued: "Queued",
  working: "Working…",
  waiting: "Waiting",
  needs_input: "Needs your input",
  needs_approval: "Needs your approval",
  scheduled: "Scheduled",
  monitoring: "Monitoring",
  completed: "Done",
  failed: "Didn't finish",
  cancelled: "Cancelled",
};

export function stateLabel(state: string): string {
  return STATE_LABEL[state as TaskState] ?? state;
}

export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return then.toLocaleDateString(undefined, { day: "numeric", month: "short", year: then.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}

export function fullDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
