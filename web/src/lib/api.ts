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
  /** `reason`: why it isn't ready, so Manage can offer the one thing that fixes it. */
  availability: { state: string; note: string | null; reason?: "ready" | "paused" | "waiting_for_worker" | "optional_worker" | "needs_start" | "needs_credential" | "unreachable" | "nothing_usable" | string };
  secret_names: string[];
  /** What the connection needs and whether it has it. Values are never sent. */
  credentials: { note?: string | null; why?: string | null; name: string; label: string; present: boolean; source?: "bevro" | "host" | "project" | "missing" | string; status?: string }[];
  /** How this installation runs, in words. Mechanism stays on the server. */
  runtime: { display_name: string; runs_at?: string | null; availability: string; credentials_label: string; runtimes_found: number; alternatives: number; health: string; built?: boolean; review?: string | null; abilities: Record<string, boolean> } | null;
  /** How the person uses it apart from Bevro. A web app comes with how its address was published. */
  surfaces?: Surface[];
  /** Can Bevro itself send it work. "not_set_up" is not a fault. */
  direct?: { state: DirectState; note?: string };
  /** For agents Bevro created: what it is for and which version is in use. */
  build?: { purpose: string; version: number; state: string; built_by: string | null; needs: string[]; can_rebuild: boolean } | null;
  created_at: string;
  updated_at: string;
}

export type DirectState = "ready" | "needs_credential" | "waiting_for_worker" | "needs_start" | "unreachable" | "paused" | "not_set_up";

/** One way of using an app or agent, or one place its results go. See docs/HUB.md. */
export interface Surface {
  kind: "web_app" | "telegram" | "slack" | "discord" | "schedule" | "command_line" | string;
  /** use: the person uses it there. delivers: it sends results there. runs: it works by itself. */
  role: "use" | "delivers" | "runs" | string;
  label: string;
  sentence: string;
  url?: string;
  /** How the address was published, which decides where a browser can open it from. */
  reach?: "explicit" | "shared" | "network" | "all_interfaces" | "loopback" | string;
  /** For a shared address: the one on this machine behind it. */
  local_url?: string;
  /** Every address a browser might open it at, most suitable in general first. The browser picks (lib/hub.ts). */
  candidates?: { url: string; reach: string }[];
  title?: string;
  when?: string | null;
  installed?: boolean | null;
}

/** What Bevro is asking permission for: one folder, or one program. */
export interface TrustAsk {
  kind: "folder" | "command";
  label: string;
  /** The resolved path - where it really is, once shortcuts are followed. */
  path?: string | null;
  parent?: string | null;
  parent_label?: string | null;
  program?: string | null;
  is_directory?: boolean;
  exists?: boolean;
}

export interface TrustGrant {
  id: string;
  kind: "folder" | "command" | "context";
  label: string;
  target: string;
  scope: string;
  granted_at: string | null;
  granted_by: string;
}

export interface ProviderDetails {
  id: string;
  active_runtime: Record<string, unknown> | null;
  runtimes: {
    id: string;
    kind: string;
    adapter: string;
    display_name: string;
    availability: string;
    active: boolean;
    credential_strategy: string;
    /** What this way in needs and who has it, said plainly. */
    credential_summary?: string;
    reachable_from?: string | null;
    usable?: boolean;
    why_not?: string | null;
  }[];
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
  location_class?: string | null;
  /** Things that launch the project's program with an environment of their own. */
  contexts?: ExecutionContextView[];
}

export interface ExecutionContextView {
  id: string;
  /** "Installed system service", "Compose service"... */
  title: string;
  /** The unit, or the Compose service and its file. Never a path. */
  name: string;
  /** Credential names only: "Provides OPENAI_API_KEY". */
  credentials: string;
  takes_work: string;
  needs_admin: string;
  authorised: string;
  available: boolean;
  why_not: string[];
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
  /** Bevro can't tell what it is for, and nobody has said yet. */
  needs_description?: boolean;
  /** The person has said what it is for. */
  described?: boolean;
  evidence: string[];
  warnings: string[];
  app_url: string | null;
  /**
   * A page for people answered: something is running. Never a way to send it
   * work. `routes` are paths its website passes on to another part of it.
   */
  web_ui?: { running: boolean; title: string | null; routes: string[] } | null;
  /** How the person uses it apart from Bevro. */
  surfaces?: Surface[];
  /** Bevro can't send it work, but it is worth adding: the person uses it somewhere, or has said what it's for. */
  can_add?: boolean;
  /** `why`: the optional explanation behind `hint`, for "Why?". */
  auth: { required: boolean; secret_name: string | null; label: string | null; hint: string | null; why?: string | null };
  invocable: boolean;
}

/** One fact a test checked. `ok: null` means "not checked", and the label says why. */
export interface TestCheck {
  label: string;
  ok: boolean | null;
  kind?: "credential" | "functional" | string;
  detail?: string | null;
}

export interface TestResult {
  ok: boolean;
  detail: string | null;
  checks?: TestCheck[];
  /** The one thing that would fix it, when there is one. */
  next?: "add_credential" | string | null;
}

export interface ConnectDraft {
  id: string;
  state: "looking" | "choice_required" | "trust_required" | "found" | "failed" | "testing" | "connected";
  target_kind: string;
  target_label: string;
  draft: DraftView | null;
  /** When Bevro needs permission before it looks at something on this machine. */
  trust?: TrustAsk | null;
  /** When a name fits more than one folder on this machine: which did you mean? */
  choices?: { label: string; where: string }[] | null;
  /** Found by what it is called, on this machine. */
  found_by_name?: boolean;
  error: string | null;
  /** What kind of failure, so the page can offer the right way out. */
  problem?: "worker" | "unreachable" | "not_found" | "failed" | null;
  /** What was found is already connected: point at it instead. */
  already_connected?: { id: string; name: string } | null;
  test: TestResult | null;
  provider_id: string | null;
  created_at: string;
}

export type HealthOut = TestResult;

export class ApiError extends Error {
  status: number;
  /** Short machine code from the API, e.g. "no_provider", "use_elsewhere". */
  reason: string | null;
  /** With "use_elsewhere": the app the person has for this. */
  suggestion: { id: string; name: string } | null;
  constructor(status: number, message: string, reason: string | null = null, suggestion: { id: string; name: string } | null = null) {
    super(message);
    this.status = status;
    this.reason = reason;
    this.suggestion = suggestion;
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
    let suggestion: { id: string; name: string } | null = null;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") message = body.detail;
      else if (Array.isArray(body?.detail) && typeof body.detail[0]?.msg === "string") message = String(body.detail[0].msg).replace(/^Value error, /, "");
      else if (body?.detail && typeof body.detail.message === "string") {
        message = body.detail.message;
        reason = typeof body.detail.reason === "string" ? body.detail.reason : null;
        const s = body.detail.suggestion;
        suggestion = s && typeof s.id === "string" && typeof s.name === "string" ? { id: s.id, name: s.name } : null;
      }
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(res.status, message, reason, suggestion);
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
  getProvider: (id: string) => request<Provider>(`/providers/${id}`),
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
  updateProvider: (id: string, body: { enabled?: boolean; name?: string; description?: string; web_address?: string }) =>
    request<Provider>(`/providers/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  checkProvider: (id: string) => request<HealthOut>(`/providers/${id}/check`, { method: "POST" }),
  providerDetails: (id: string) => request<ProviderDetails>(`/providers/${id}/details`),
  connectDescribe: (id: string, capability_summary: string, name?: string) =>
    request<ConnectDraft>(`/connect/drafts/${id}/describe`, { method: "POST", body: JSON.stringify(name ? { capability_summary, name } : { capability_summary }) }),
  connectChoose: (id: string, choice: number) =>
    request<ConnectDraft>(`/connect/drafts/${id}/choose`, { method: "POST", body: JSON.stringify({ choice }) }),
  connectAllow: (id: string, scope: "exact" | "parent") =>
    request<ConnectDraft>(`/connect/drafts/${id}/trust`, { method: "POST", body: JSON.stringify({ scope }) }),
  access: () => request<{ grants: TrustGrant[]; ceiling: string[] }>("/trust"),
  revokeGrant: (id: string) => request<void>(`/trust/${id}`, { method: "DELETE" }),
  reconnectProvider: (id: string) => request<Provider>(`/providers/${id}/reconnect`, { method: "POST" }),
  /** Advanced setup for something already in Bevro: attach a way in to that same item. */
  addDirectAccess: (id: string, body: { method: string; details: Record<string, string>; secrets: Record<string, string> }) =>
    request<Provider>(`/providers/${id}/direct-access`, { method: "POST", body: JSON.stringify(body) }),
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
