/** Thin client for the Bevro API. All paths are relative to /api. */

export interface ProviderRef {
  /** Absent once the agent has been removed; the name is still the one it had. */
  id: string | null;
  slug: string | null;
  name: string;
  removed?: boolean;
}

/** What removing an agent would take with it. */
export interface RemovalPlan {
  removable: boolean;
  /** Pieces of work it did. These are kept. */
  history: number;
  /** Work running right now, which must finish or be cancelled first. */
  in_flight: number;
  credentials: number;
  built_project: boolean;
  built_connection: boolean;
}

export interface Capability {
  id: string;
  title?: string;
  description?: string;
  [key: string]: unknown;
}

export interface Provider {
  id: string;
  slug: string;
  name: string;
  /** One short sentence for the card. Never the service's own interface prose. */
  description: string;
  /** Plain English, for "More details": what it does, how it connects. */
  details?: { what_it_does?: string; how_it_connects?: string };
  enabled: boolean;
  capabilities: Capability[];
  app_url: string | null;
  icon: { kind: string; text?: string } | null;
  origin: "example" | "created" | "connected" | string;
  actions: string[];
  connection: string | null;
  availability: { state: string; note: string | null };
  secret_names: string[];
  /** What the connection needs and whether it has it. Values are never sent. */
  credentials: { name: string; label: string; present: boolean; source?: "bevro" | "host" | "project" | "missing" | string; status?: string }[];
  /** How this installation runs, in words. Mechanism stays on the server. */
  runtime: { display_name: string; runs_at?: string | null; availability: string; credentials_label: string; runtimes_found: number; alternatives: number; health: string; built?: boolean; review?: string | null; abilities: Record<string, boolean> } | null;
  /** For agents Bevro created: what it is for and which version is in use. */
  build?: { purpose: string; version: number; state: string; built_by: string | null; needs: string[]; can_rebuild: boolean } | null;
  created_at: string;
  updated_at: string;
}

export interface ProviderDetails {
  id: string;
  active_runtime: Record<string, unknown> | null;
  runtimes: { id: string; kind: string; adapter: string; display_name: string; availability: string; active: boolean; credential_strategy: string }[];
  source_kind: string | null;
  /** The exact name the service gave, when Bevro shows a shorter one. */
  source_name?: string | null;
  /** The address or folder that was typed into Connect, credentials stripped. */
  source_target?: string | null;
  /** What the service said about itself when Bevro found it. */
  source_description?: string | null;
  operation_count?: number | null;
  runs_at?: string | null;
  reachability?: { api?: string; worker?: string; api_checked_at?: string | null; worker_checked_at?: string | null } | null;
}

export type TaskState =
  | "created"
  | "queued"
  | "working"
  | "waiting"
  | "needs_input"
  | "needs_approval"
  | "scheduled"
  | "monitoring"
  | "completed"
  | "failed"
  | "cancelled";

export interface Task {
  id: string;
  title: string;
  original_request: string;
  state: TaskState;
  summary: string | null;
  provider: ProviderRef | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface FailureAction {
  kind: "retry" | "add_credential" | "test_connection" | "manage" | string;
  label: string;
  secret_name: string | null;
  secret_label: string | null;
}

/** Why a run did not finish, in words a person can act on. Never technical. */
export interface Failure {
  category: "credential_required" | "provider_unavailable" | "execution_failed" | "configuration_problem" | string;
  title: string;
  message: string;
  actions: FailureAction[];
}

export interface Run {
  id: string;
  provider: ProviderRef;
  state: string;
  result_summary: string | null;
  error_summary: string | null;
  failure: Failure | null;
  /** The work succeeded only after Bevro tried another way of reaching the provider. */
  recovered: boolean;
  phase: string | null;
  steps: string[];
  workspace: { id: string; name: string } | null;
  permissions: string[];
  started_at: string | null;
  completed_at: string | null;
}

export interface InputRequest {
  question: string;
  kind: string;
  options: { value: string; label: string }[];
}

export interface Workspace {
  id: string;
  name: string;
  description: string;
  permissions: string[];
}

export interface Artifact {
  id: string;
  task_id: string;
  provider_run_id: string | null;
  type: string;
  title: string;
  summary: string | null;
  mime_type: string | null;
  payload: Record<string, unknown> | unknown[] | null;
  external_url: string | null;
  content_url: string | null;
  metadata: Record<string, unknown>;
  known: boolean;
  created_at: string;
}

export interface TaskDetail extends Task {
  runs: Run[];
  artifacts: Artifact[];
  input_request: InputRequest | null;
}

export interface CreatePreview {
  name: string;
  description: string;
  /** What it will be able to do, in plain words. */
  can: string[];
  /** What it will need, in plain words. */
  needs: string[];
  produces: string;
  schedule: string | null;
  /** False when no connected agent can build it. */
  can_build: boolean;
  /** The agreed description, handed back when building. Never shown. */
  spec: Record<string, unknown>;
  capabilities: Capability[];
  permissions: string[];
  enabled: boolean;
}

export interface CreateStatus {
  state: "designing" | "building" | "testing" | "connecting" | "ready" | "failed" | string;
  note: string;
  provider_id: string;
  task_id: string | null;
  steps: string[];
  detail?: string | null;
}

export interface Automation {
  id: string;
  title: string;
  instruction: string;
  mode: "scheduled" | "monitoring" | string;
  enabled: boolean;
  /** "Every Monday · 09:00" */
  schedule: string;
  /** Monitoring only: "Notify when the result changes" */
  condition: string | null;
  provider: ProviderRef | null;
  next_run_at: string | null;
  last_run_at: string | null;
  /** "No change last run" / "Ran yesterday" */
  last_result: string | null;
  /** How the person wants to hear about this one. */
  notify: NotifyPreference;
  created_at: string;
}

/** Where a result that matters should reach the person. */
export interface NotifyPreference {
  in_app: boolean;
  email: boolean;
  webhook: boolean;
  /** Scheduled work only: tell me every time it finishes. */
  on_finish: boolean;
  /** Optional: somewhere of its own. Empty means the installation's default. */
  email_to: string;
  webhook_url: string;
}

/** Something worth telling the person about. The work itself is the Task. */
export interface Notification {
  id: string;
  kind: "automation.matched" | "automation.finished" | "automation.failed" | string;
  title: string;
  summary: string | null;
  reason: string | null;
  read: boolean;
  task_id: string | null;
  automation_id: string | null;
  /** "in_app" | "sending" | "delivered" | "partly_delivered" | "delivery_failed" */
  delivery: string;
  channels: string[];
  /** Why an external message didn't go, in words. Never a server's own. */
  delivery_problem: string | null;
  created_at: string;
}

export interface NotificationList {
  unread: number;
  items: Notification[];
}

/** A way of being told, and whether this installation can use it. */
export interface DeliveryChannelInfo {
  name: "in_app" | "email" | "webhook" | string;
  label: string;
  /** Usable with nothing more to type. */
  available: boolean;
  /** Worth offering: the machinery is here, even if an address is not. */
  offerable: boolean;
  external: boolean;
  /** True when one automation may give an address of its own. */
  accepts_destination: boolean;
  /** True when the person must supply that address themselves. */
  needs_destination: boolean;
  note: string | null;
}

export interface AutomationDetail extends Automation {
  runs: { id: string; task_id: string | null; scheduled_for: string; started_at: string | null; delayed: boolean; trigger: string; outcome: string | null; matched: boolean | null; reason: string | null }[];
  recurrence: string | null;
  timezone: string | null;
  selection: string | null;
}

/** What Bevro would set up, shown before anything recurring is created. */
export interface ScheduleIntent {
  recurring: boolean;
  title: string | null;
  instruction: string | null;
  schedule: string | null;
  condition: string | null;
  mode: string | null;
}

export interface Meta {
  name: string;
  version: string;
  tagline: string;
  routing?: { mode: "llm" | "deterministic" };
}

/** What discovery found, as the browser may see it: no adapter details, no paths. */
export interface RuntimeView {
  id: string;
  display_name: string;
  availability: string;
  confidence: string;
  credentials: { label: string; required_from_user: boolean; names: string[]; note: string | null };
  abilities: Record<string, boolean>;
  evidence: string[];
  warnings: string[];
  invocable: boolean;
}

export interface BridgeStatus {
  state: "preparing" | "building" | "testing" | "ready" | "failed" | string;
  note: string;
  provider_id: string;
  task_id: string | null;
  steps: string[];
}

export interface DraftView {
  /** True when the project has useful code but nothing that can take a task. */
  needs_bridge?: boolean;
  /** True when some connected agent could build that connection. */
  bridge_possible?: boolean;
  runs_via: string | null;
  runs_at: string | null;   // "On this machine" when only the worker can reach it
  /** What the thing said about itself: shown under "How Bevro found this", never as the description. */
  source_description?: string | null;
  runtime: RuntimeView | null;
  runtime_options: RuntimeView[];
  runtimes_found: number;
  choice_needed: boolean;
  /** A scoped service with several to pick from: which profile, workspace, tenant. */
  scope_choices?: { value: string; label: string }[];
  /** The one it is already scoped to, in words: "EU West". */
  connected_for?: string | null;
  credentials_label: string | null;
  name: string;
  description: string;
  capabilities: Capability[];
  mechanism: string;
  mechanism_label: string;
  invocation_label: string | null;
  availability: "ready" | "needs_worker" | "needs_start" | "not_invocable";
  confidence: "high" | "medium" | "low";
  confidence_label: string;
  note: string | null;
  evidence: string[];
  warnings: string[];
  app_url: string | null;
  auth: { required: boolean; secret_name: string | null; label: string | null; hint: string | null };
  invocable: boolean;
}

export interface ConnectDraft {
  id: string;
  state: "looking" | "found" | "failed" | "testing" | "connected";
  target_kind: string;
  target_label: string;
  draft: DraftView | null;
  error: string | null;
  test: { ok: boolean; detail: string | null } | null;
  provider_id: string | null;
  created_at: string;
}

export interface HealthOut {
  ok: boolean;
  detail: string | null;
}

export class ApiError extends Error {
  status: number;
  /** Short machine code from the API, e.g. "no_provider". */
  reason: string | null;
  constructor(status: number, message: string, reason: string | null = null) {
    super(message);
    this.status = status;
    this.reason = reason;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
  });
  if (!res.ok) {
    let message = "Something went wrong.";
    let reason: string | null = null;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") message = body.detail;
      else if (body?.detail && typeof body.detail.message === "string") {
        message = body.detail.message;
        reason = typeof body.detail.reason === "string" ? body.detail.reason : null;
      }
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(res.status, message, reason);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  meta: () => request<Meta>("/meta"),
  submitTask: (body: { request: string; provider_id?: string }) =>
    request<TaskDetail>("/tasks", { method: "POST", body: JSON.stringify(body) }),
  listTasks: (params: { q?: string; state?: string } = {}) => {
    const qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.state) qs.set("state", params.state);
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<Task[]>(`/tasks${suffix}`);
  },
  getTask: (id: string) => request<TaskDetail>(`/tasks/${id}`),
  cancelTask: (id: string) => request<TaskDetail>(`/tasks/${id}/cancel`, { method: "POST" }),
  /** Remove one task and its results. The agent that did it is untouched. */
  removeTask: (id: string) => request<void>(`/tasks/${id}`, { method: "DELETE" }),
  /** Forget all finished work. Agents, scheduled work and settings stay. */
  clearHistory: () => request<{ removed: number }>("/tasks", { method: "DELETE" }),
  retryTask: (id: string) => request<TaskDetail>(`/tasks/${id}/retry`, { method: "POST" }),
  answerInput: (id: string, value: string) =>
    request<TaskDetail>(`/tasks/${id}/input`, { method: "POST", body: JSON.stringify({ value }) }),
  listWorkspaces: () => request<Workspace[]>("/workspaces"),
  scheduleIntent: (text: string, timezone: string) =>
    request<ScheduleIntent>("/automations/intent", { method: "POST", body: JSON.stringify({ text, timezone }) }),
  listAutomations: () => request<Automation[]>("/automations"),
  getAutomation: (id: string) => request<AutomationDetail>(`/automations/${id}`),
  createAutomation: (body: { when: string; instruction?: string; task_id?: string; only_when?: string; notify?: NotifyPreference; timezone: string }) =>
    request<Automation>("/automations", { method: "POST", body: JSON.stringify(body) }),
  updateAutomation: (id: string, body: { when?: string; instruction?: string; only_when?: string; enabled?: boolean; notify?: NotifyPreference; timezone: string }) =>
    request<Automation>(`/automations/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  runAutomation: (id: string) => request<Automation>(`/automations/${id}/run`, { method: "POST" }),
  removeAutomation: (id: string) => request<void>(`/automations/${id}`, { method: "DELETE" }),
  listNotifications: (unreadOnly = false) =>
    request<NotificationList>(`/notifications${unreadOnly ? "?unread_only=true" : ""}`),
  deliveryChannels: () => request<DeliveryChannelInfo[]>("/notifications/channels"),
  markNotificationRead: (id: string) => request<Notification>(`/notifications/${id}/read`, { method: "POST" }),
  markAllNotificationsRead: () => request<NotificationList>("/notifications/read-all", { method: "POST" }),
  retryNotification: (id: string) => request<Notification>(`/notifications/${id}/retry`, { method: "POST" }),
  dismissNotification: (id: string) => request<void>(`/notifications/${id}`, { method: "DELETE" }),
  listProviders: () => request<Provider[]>("/providers"),
  /** Advanced setup: the technical escape hatch. Normal Connect goes through connectDiscover. */
  connectProvider: (body: {
    name: string;
    description: string;
    capabilities: string[];
    method: "api" | "mcp" | "local" | "command";
    details: Record<string, string>;
    secrets: Record<string, string>;
    app_url: string | null;
  }) => request<Provider>("/providers", { method: "POST", body: JSON.stringify(body) }),
  connectDiscover: (target: string, secrets: Record<string, string> = {}) =>
    request<ConnectDraft>("/connect/discover", { method: "POST", body: JSON.stringify({ target, secrets }) }),
  connectDraft: (id: string) => request<ConnectDraft>(`/connect/drafts/${id}`),
  connectBridge: (id: string) => request<BridgeStatus>(`/connect/drafts/${id}/bridge`, { method: "POST" }),
  bridgeStatus: (providerId: string) => request<BridgeStatus>(`/connect/bridges/${providerId}`),
  rebuildProvider: (id: string) => request<{ ok: boolean; detail: string | null }>(`/providers/${id}/rebuild`, { method: "POST" }),
  connectTest: (id: string, secrets: Record<string, string> = {}) =>
    request<ConnectDraft>(`/connect/drafts/${id}/test`, { method: "POST", body: JSON.stringify({ secrets }) }),
  connectConfirm: (id: string, body: { name?: string; description?: string; capability_summary?: string; secrets?: Record<string, string>; app_url?: string | null; runtime_id?: string; scope?: string }) =>
    request<Provider>(`/connect/drafts/${id}/confirm`, { method: "POST", body: JSON.stringify(body) }),
  updateProvider: (id: string, body: { enabled?: boolean; name?: string; description?: string }) =>
    request<Provider>(`/providers/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  checkProvider: (id: string) => request<HealthOut>(`/providers/${id}/check`, { method: "POST" }),
  providerDetails: (id: string) => request<ProviderDetails>(`/providers/${id}/details`),
  reconnectProvider: (id: string) => request<Provider>(`/providers/${id}/reconnect`, { method: "POST" }),
  putSecret: (id: string, name: string, value: string) =>
    request<Provider>(`/providers/${id}/secrets/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ value }) }),
  removalPlan: (id: string) => request<RemovalPlan>(`/providers/${id}/removal`),
  removeProvider: (id: string) => request<void>(`/providers/${id}`, { method: "DELETE" }),
  createPreview: (description: string) =>
    request<CreatePreview>("/create/preview", { method: "POST", body: JSON.stringify({ description }) }),
  createBuild: (preview: CreatePreview, description: string) =>
    request<CreateStatus>("/create/build", { method: "POST", body: JSON.stringify({ spec: preview.spec, description }) }),
  createStatus: (providerId: string) => request<CreateStatus>(`/create/builds/${providerId}`),
  createRebuild: (providerId: string, description?: string) =>
    request<CreateStatus>(`/create/builds/${providerId}/rebuild`, { method: "POST", body: JSON.stringify({ description: description ?? "" }) }),
};

export const TERMINAL_STATES: ReadonlySet<TaskState> = new Set(["completed", "failed", "cancelled"]);
export const isTerminal = (state: TaskState) => TERMINAL_STATES.has(state);
