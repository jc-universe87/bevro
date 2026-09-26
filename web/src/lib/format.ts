import { isTerminal, type Task, type TaskState, type TaskStatusInfo } from "./api";

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

const KIND_OF: Partial<Record<TaskState, TaskStatusInfo["kind"]>> = {
  created: "starting",
  queued: "starting",
  needs_input: "needs_you",
  needs_approval: "needs_you",
  completed: "completed",
  failed: "failed",
  cancelled: "stopped",
};

/** The task's status as the server put it; derived from the bare state only for old responses. */
export function statusOf(task: Task): TaskStatusInfo {
  if (task.status) return task.status;
  const label = stateLabel(task.state);
  return { kind: KIND_OF[task.state] ?? "working", label, headline: label, note: null, quiet: false, since: null, can_cancel: !isTerminal(task.state) };
}

/** "3 min" - how long something has been going, in the largest sensible unit. */
export function elapsed(iso: string, now: Date = new Date()): string {
  const seconds = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "less than a minute";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min`;
}
