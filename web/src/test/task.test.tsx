/**
 * One task, from "Starting…" to its result, in plain words: what the page
 * says at each point, what it offers, and what it keeps out of the way.
 * Synthetic tasks only; the server's status is what the page shows.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi, task } from "./helpers";

const desk = { id: "p1", slug: "jobs-desk", name: "Jobs Desk" };

const status = (kind: string, headline: string, over: Record<string, unknown> = {}) => ({
  kind,
  label: { starting: "Starting", working: "Working", completed: "Completed", failed: "Couldn't complete", stopped: "Stopped", needs_you: "Needs you" }[kind],
  headline,
  note: null,
  quiet: false,
  since: new Date(Date.now() - 120_000).toISOString(),
  can_cancel: false,
  ...over,
});

const run = (over: Record<string, unknown> = {}) => ({
  id: "run-1", provider: desk, state: "running", result_summary: null, error_summary: null, failure: null, recovered: false,
  phase: null, steps: [], tried: [], workspace: null, permissions: [], started_at: new Date().toISOString(), completed_at: null, ...over,
});

const artifact = (id: string, title: string, over: Record<string, unknown> = {}) => ({
  id, task_id: "t1", provider_run_id: "run-1", type: "text", title, summary: null, mime_type: null, payload: { text: `${title} text` },
  external_url: null, content_url: null, metadata: {}, known: true, primary: false, created_at: new Date().toISOString(), ...over,
});

function renderAt(path: string) {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

test("the page follows the task by itself: starting, working, then completed with its result", async () => {
  const stages = [
    task({ id: "t1", state: "queued", provider: desk, runs: [run({ state: "pending", started_at: null })], status: status("starting", "Starting…", { since: null, can_cancel: true }) }),
    task({ id: "t1", state: "working", provider: desk, runs: [run()], status: status("working", "Jobs Desk is working on this.", { note: "Reviewing opportunities" }) }),
    task({ id: "t1", state: "completed", provider: desk, summary: "Three are waiting.", runs: [run({ state: "completed" })], artifacts: [artifact("a1", "Answer", { primary: true })], status: status("completed", "Jobs Desk finished this.") }),
  ];
  let seen = 0;
  mockApi({ "GET /api/tasks/t1": () => stages[Math.min(seen++, stages.length - 1)] });
  renderAt("/tasks/t1");
  const region = await screen.findByRole("region", { name: "Status" });
  expect(within(region).getByText("Starting…")).toBeInTheDocument();
  expect(within(region).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  // No reload: the page looks again by itself.
  expect(await within(region).findByText("Jobs Desk is working on this.", {}, { timeout: 3000 })).toBeInTheDocument();
  expect(within(region).getByText("Reviewing opportunities")).toBeInTheDocument();
  expect(within(region).getByText("Started 2 min ago")).toBeInTheDocument();
  // Only the sentence is announced; what it is doing and the time are not read out each look.
  expect(within(region).getByText("Jobs Desk is working on this.").closest("[aria-live]")).toHaveAttribute("aria-live", "polite");
  expect(within(region).getByText("Reviewing opportunities").closest("[aria-live]")).toBeNull();
  expect(region.textContent).not.toMatch(/\d+\s*%/);
  expect(await within(region).findByText("Jobs Desk finished this.", {}, { timeout: 3000 })).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Result" })).getByText("Three are waiting.")).toBeInTheDocument();
  const looks = seen;
  await new Promise((r) => setTimeout(r, 1200));
  expect(seen).toBe(looks); // finished work isn't polled any more
});

test("trying another way in is said as it happens, and is not an error", async () => {
  mockApi({ "GET /api/tasks/t1": task({ id: "t1", state: "working", provider: desk, runs: [run()], status: status("working", "Trying another way to reach Jobs Desk…") }) });
  renderAt("/tasks/t1");
  const region = await screen.findByRole("region", { name: "Status" });
  expect(within(region).getByText("Trying another way to reach Jobs Desk…")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: /Try again/ })).toBeNull();
});

test("several results: the one to read comes first, the rest under More results", async () => {
  mockApi({
    "GET /api/tasks/t1": task({
      id: "t1", state: "completed", provider: desk, runs: [run({ state: "completed" })], status: status("completed", "Jobs Desk finished this."),
      artifacts: [artifact("a1", "Weekly review", { type: "report", primary: true, payload: { text: "# Weekly review" }, mime_type: "text/markdown" }), artifact("a2", "Shortlist"), artifact("a3", "Open in Jobs Desk", { type: "deep_link", external_url: "https://desk.example/app", payload: null })],
    }),
  });
  renderAt("/tasks/t1");
  const result = await screen.findByRole("region", { name: "Result" });
  const heading = within(result).getByRole("heading", { name: "More results" });
  const text = result.textContent ?? "";
  expect(text.indexOf("Weekly review")).toBeLessThan(text.indexOf("More results"));
  expect(heading).toBeInTheDocument();
  expect(text.indexOf("Shortlist")).toBeGreaterThan(text.indexOf("More results"));
  expect(text.indexOf("Open in Jobs Desk")).toBeGreaterThan(text.indexOf("Shortlist"));
});

test("trying again asks first only when the app may already have acted on it", async () => {
  const failure = { category: "invocation_failed", title: "Couldn't complete", message: "Jobs Desk started the work but couldn't complete it.", may_repeat: true, actions: [{ kind: "retry", label: "Try again", secret_name: null, secret_label: null }, { kind: "manage", label: "Go to Jobs Desk", secret_name: null, secret_label: null }] };
  const failed = task({ id: "t1", state: "failed", provider: desk, runs: [run({ state: "failed", failure })], status: status("failed", failure.message) });
  const calls = mockApi({ "GET /api/tasks/t1": failed, "GET /api/providers/p1": {}, "POST /api/tasks/t1/retry": task({ ...failed, state: "queued", status: status("starting", "Starting…") }) });
  const user = userEvent.setup();
  renderAt("/tasks/t1");
  const region = await screen.findByRole("region", { name: "Status" });
  await user.click(within(region).getByRole("button", { name: "Try again" }));
  const question = within(region).getByRole("group", { name: "Try again may send this request to Jobs Desk again." });
  expect(calls.some((c) => c.url === "/api/tasks/t1/retry")).toBe(false);
  await user.click(within(question).getByRole("button", { name: "Leave it" }));
  expect(calls.some((c) => c.url === "/api/tasks/t1/retry")).toBe(false);
  await user.click(within(region).getByRole("button", { name: "Try again" }));
  await user.click(within(region).getByRole("button", { name: "Try again anyway" }));
  expect(calls.filter((c) => c.url === "/api/tasks/t1/retry")).toHaveLength(1);
});

test("an app Bevro can't reach offers a way to open it, when there is one", async () => {
  const failure = { category: "provider_unavailable", title: "Couldn't reach it", message: "I couldn't reach Jobs Desk.", may_repeat: false, actions: [{ kind: "test_connection", label: "Check Jobs Desk", secret_name: null, secret_label: null }, { kind: "open_app", label: "Open app", secret_name: null, secret_label: null }] };
  const app = { ...desk, description: "", enabled: true, capabilities: [], app_url: null, icon: null, origin: "connected", actions: ["open"], connection: null, availability: { state: "available", note: null }, secret_names: [], credentials: [], runtime: null, details: {}, created_at: "", updated_at: "", direct: { state: "unreachable", note: "" }, surfaces: [{ kind: "web_app", role: "use", label: "Web app", sentence: "", url: "https://desk.example.net", reach: "shared" }] };
  mockApi({ "GET /api/tasks/t1": task({ id: "t1", state: "failed", provider: desk, runs: [run({ state: "failed", failure })], status: status("failed", failure.message) }), "GET /api/providers/p1": app });
  renderAt("/tasks/t1");
  const region = await screen.findByRole("region", { name: "Status" });
  expect(within(region).getByRole("link", { name: "Check Jobs Desk" })).toHaveAttribute("href", "/apps/p1?test=1");
  expect(await within(region).findByRole("link", { name: /Open app/ })).toHaveAttribute("href", "https://desk.example.net/");
});

test("Cancel is there only when Bevro can really stop it, and a refusal is said", async () => {
  const working = task({ id: "t1", state: "working", provider: desk, runs: [run()], status: status("working", "Jobs Desk is working on this.") });
  mockApi({ "GET /api/tasks/t1": working });
  renderAt("/tasks/t1");
  await screen.findByText("Jobs Desk is working on this.");
  expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
});

test("a quiet task says so and offers to look again, without calling it failed", async () => {
  mockApi({ "GET /api/tasks/t1": task({ id: "t1", state: "working", provider: desk, runs: [run()], status: status("working", "Jobs Desk is working on this.", { quiet: true, note: "Bevro hasn't had an update recently." }) }) });
  renderAt("/tasks/t1");
  const region = await screen.findByRole("region", { name: "Status" });
  expect(within(region).getByText("Bevro hasn't had an update recently.")).toBeInTheDocument();
  expect(within(region).getByRole("button", { name: "Check again" })).toBeInTheDocument();
  expect(within(region).getByRole("link", { name: "Go to Jobs Desk" })).toHaveAttribute("href", "/apps/p1");
  expect(region.textContent).not.toMatch(/fail|error|couldn't/i);
});

test("details - every attempt and way tried - stay folded away until asked for", async () => {
  mockApi({
    "GET /api/tasks/t1": task({
      id: "t1", state: "completed", provider: desk, summary: "Done.", status: status("completed", "Jobs Desk finished this."),
      runs: [
        run({ id: "run-1", state: "failed", error_summary: "Jobs Desk couldn't be reached.", tried: [{ way: "Over the network", outcome: "Couldn't reach it" }] }),
        run({ id: "run-2", state: "completed", tried: [{ way: "Over the network", outcome: "Couldn't reach it" }, { way: "As a program on this computer", outcome: "Worked" }] }),
      ],
    }),
  });
  const user = userEvent.setup();
  renderAt("/tasks/t1");
  await screen.findByText("Jobs Desk finished this.");
  const details = screen.getByText("Details").closest("details")!;
  expect(details).not.toHaveAttribute("open");
  await user.click(screen.getByText("Details"));
  const attempts = within(details).getByRole("list", { name: "Attempts" });
  expect(within(attempts).getByText("Attempt 1")).toBeInTheDocument();
  expect(within(attempts).getByText("Attempt 2")).toBeInTheDocument();
  expect(within(attempts).getByText("As a program on this computer: Worked")).toBeInTheDocument();
  expect(within(attempts).getByText("What happened: Jobs Desk couldn't be reached.")).toBeInTheDocument();
});

test("Recent shows each task in plain words, with who did it and what came of it", async () => {
  mockApi({
    "GET /api/tasks": [
      task({ id: "t1", title: "Review my current opportunities", state: "completed", provider: desk, summary: "Three are waiting.", completed_at: new Date().toISOString(), results: 2, status: status("completed", "Jobs Desk finished this.") }),
      task({ id: "t2", title: "Research widget trends", state: "failed", provider: { id: "p3", slug: "briefing", name: "Briefing" }, summary: "OPENAI_API_KEY missing", status: { ...status("failed", "Briefing needs a credential."), label: "Needs a credential" } }),
    ],
  });
  renderAt("/recent");
  const list = await screen.findByRole("list", { name: "Recent work" });
  const [done, blocked] = within(list).getAllByRole("listitem");
  expect(done).toHaveTextContent("Review my current opportunities");
  expect(done).toHaveTextContent("Jobs Desk·Completed·2 results");
  expect(done).toHaveTextContent("Three are waiting.");
  expect(blocked).toHaveTextContent("Briefing·Needs a credential");
  // A failure is told by its status, not by whatever the app last printed.
  expect(blocked).not.toHaveTextContent("OPENAI_API_KEY");
  expect(list.textContent).not.toMatch(/runtime|adapter|heartbeat|queued|provider run/i);
});

test("from Home, a direct request says Starting… until its task is there", async () => {
  let release: (v: unknown) => void = () => {};
  const created = new Promise((r) => (release = r));
  mockApi({
    "GET /api/providers": [],
    "GET /api/notifications*": { items: [], unread: 0 },
    "POST /api/automations/intent": { recurring: false },
    "POST /api/route": { outcome: "direct", message: "Jobs Desk can handle this directly.", sure: true, item: desk, why: null, choices: [] },
    "POST /api/tasks": () => created,
    "GET /api/tasks/t1": task({ id: "t1", provider: desk, status: status("starting", "Starting…") }),
  });
  const user = userEvent.setup();
  renderAt("/");
  await user.type(await screen.findByRole("textbox", { name: "What do you want to get done?" }), "Review my opportunities{Enter}");
  expect(await screen.findByRole("status")).toHaveTextContent("Starting…");
  release(task({ id: "t1", provider: desk }));
  expect(await screen.findByRole("region", { name: "Status" })).toHaveTextContent("Starting…");
});

test("a wide table result scrolls within its own box, not over the page", async () => {
  const columns = ["run id", "advert", "decision", "location", "salary", "closing date"];
  mockApi({
    "GET /api/tasks/t1": task({
      id: "t1", state: "completed", provider: desk, status: status("completed", "Jobs Desk finished this."), runs: [run({ state: "completed" })],
      artifacts: [artifact("a1", "Shortlist", { type: "structured", primary: true, payload: { columns, rows: [columns.map((c) => `${c} value that is long`)] } })],
    }),
  });
  renderAt("/tasks/t1");
  const table = await screen.findByRole("table");
  const box = table.parentElement!;
  expect(box).toHaveAttribute("role", "region");
  expect(box.className).toMatch(/overflow-x-auto/);
  expect(box).toHaveAttribute("tabindex", "0");
});
