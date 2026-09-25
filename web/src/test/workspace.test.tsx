import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi, task } from "./helpers";

const provider = (over: Record<string, unknown> = {}) => ({
  id: "p1",
  slug: "support-desk",
  name: "Support Desk",
  description: "Answers customer questions",
  enabled: true,
  capabilities: [{ id: "support", title: "Support" }],
  app_url: null,
  icon: { kind: "letter", text: "S" },
  origin: "connected",
  actions: ["ask"],
  connection: "http",
  availability: { state: "available", note: null },
  secret_names: [],
  credentials: [],
  runtime: null,
  created_at: "",
  updated_at: "",
  ...over,
});

/** Claude Code as a fresh installation has it: shipped, but not usable yet. */
const notSetUp = provider({
  id: "p2",
  slug: "claude-code",
  name: "Claude Code",
  description: "Build, fix and change software",
  origin: "example",
  actions: [],
  availability: { state: "unavailable", note: "Optional · needs the host worker" },
});

function renderAt(path: string) {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

test("a fresh workspace says it has no apps or agents and offers the two ways to get one", async () => {
  mockApi({
    "GET /api/providers": [notSetUp],
    "GET /api/tasks": [],
    "GET /api/notifications*": { unread: 0, items: [] },
  });
  renderAt("/apps");

  const empty = await screen.findByRole("region", { name: "Your apps and agents will appear here." });
  expect(within(empty).getByText("Connect something you already use, or create something new.")).toBeInTheDocument();
  expect(within(empty).getByRole("link", { name: "Connect" })).toHaveAttribute("href", "/connect");
  expect(within(empty).getByRole("link", { name: "Create" })).toHaveAttribute("href", "/create");

  // An agent Bevro ships that cannot be used here is not listed at all: it is
  // offered where it is needed, not kept on a shelf in Apps & agents.
  expect(screen.queryByText("Claude Code")).not.toBeInTheDocument();
  expect(screen.queryByText("Optional · needs the host worker")).not.toBeInTheDocument();
});


test("a coding agent is offered where it is actually needed", async () => {
  mockApi({
    "GET /api/providers": [notSetUp],
    "POST /api/create/preview": { name: "Price Watch", description: "Watch pricing", can: ["Monitor"], needs: ["Web access"], produces: "a summary", can_build: false, spec: {} },
    "GET /api/tasks": [],
    "GET /api/notifications*": { unread: 0, items: [] },
  });
  const user = userEvent.setup();
  renderAt("/create");

  await user.type(await screen.findByRole("textbox"), "Watch competitor pricing pages");
  await user.click(screen.getByRole("button", { name: "Create" }));

  expect(await screen.findByText("Creating agents needs a connected coding agent.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Connect coding agent" })).toHaveAttribute("href", "/connect");
});

test("once a coding agent is usable it is simply one of your agents", async () => {
  mockApi({
    "GET /api/providers": [provider({ slug: "claude-code", name: "Claude Code", origin: "example", actions: ["ask"] })],
    "GET /api/tasks": [],
    "GET /api/notifications*": { unread: 0, items: [] },
  });
  renderAt("/apps");

  const listed = await screen.findByRole("heading", { name: "Claude Code" });
  expect(listed.closest("ul")).toHaveAttribute("aria-label", "Apps and agents");
  expect(screen.queryByRole("region", { name: "Available to set up" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Your apps and agents will appear here." })).not.toBeInTheDocument();
});

test("home says what to do when nothing is connected, and suggests nothing it cannot do", async () => {
  mockApi({
    "GET /api/providers": [notSetUp],
    "GET /api/tasks": [],
    "GET /api/notifications*": { unread: 0, items: [] },
  });
  renderAt("/");

  const start = await screen.findByRole("region", { name: "Getting started" });
  expect(within(start).getByText("Start with the apps and agents you already use.")).toBeInTheDocument();
  expect(within(start).getByRole("link", { name: "Connect" })).toHaveAttribute("href", "/connect");
  expect(within(start).getByRole("link", { name: "Create" })).toHaveAttribute("href", "/create");
  // Nothing listed or suggested, because there is nothing of theirs to offer.
  expect(screen.queryByRole("navigation", { name: "Your apps and agents" })).not.toBeInTheDocument();
  expect(screen.queryByText("Claude Code")).not.toBeInTheDocument();
});

test("home offers the person's own apps and agents once there are some", async () => {
  mockApi({
    "GET /api/providers": [provider()],
    "GET /api/tasks": [],
    "GET /api/notifications*": { unread: 0, items: [] },
  });
  renderAt("/");

  const yours = await screen.findByRole("navigation", { name: "Your apps and agents" });
  expect(within(yours).getByRole("link", { name: "Support Desk" })).toHaveAttribute("href", "/apps/p1");
  expect(screen.queryByRole("region", { name: "Getting started" })).not.toBeInTheDocument();
  // No invented demo prompts.
  expect(screen.queryByText(/Compare three note-taking apps/)).not.toBeInTheDocument();
});

test("a task can be removed from Recent, with the consequences spelled out first", async () => {
  const calls = mockApi({
    "GET /api/tasks": [task({ id: "t1", title: "Compare three note-taking apps", state: "completed" })],
    "GET /api/providers": [provider()],
    "GET /api/notifications*": { unread: 0, items: [] },
    "DELETE /api/tasks/t1": null,
  });
  const user = userEvent.setup();
  renderAt("/recent");

  await user.click(await screen.findByRole("button", { name: "Remove from history" }));
  const group = screen.getByRole("group", { name: "Remove this task" });
  expect(within(group).getByText("Remove this task and its results from Bevro?")).toBeInTheDocument();
  expect(within(group).getByText(/The agent itself, and any scheduled work that asked for it, stay/)).toBeInTheDocument();

  await user.click(within(group).getByRole("button", { name: "Yes, remove" }));
  expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/tasks/t1")).toBe(true);
  expect(screen.queryByText("Compare three note-taking apps")).not.toBeInTheDocument();
});

test("changing your mind about removing a task leaves it alone", async () => {
  const calls = mockApi({
    "GET /api/tasks": [task({ id: "t1", title: "Keep me", state: "completed" })],
    "GET /api/providers": [provider()],
    "GET /api/notifications*": { unread: 0, items: [] },
  });
  const user = userEvent.setup();
  renderAt("/recent");

  await user.click(await screen.findByRole("button", { name: "Remove from history" }));
  await user.click(screen.getByRole("button", { name: "Keep it" }));
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  expect(screen.getByText("Keep me")).toBeInTheDocument();
});

test("clearing history asks first and says what survives", async () => {
  const calls = mockApi({
    "GET /api/tasks": () => [task({ id: "t1", state: "completed" })],
    "DELETE /api/tasks": { removed: 1 },
    "GET /api/providers": [provider()],
    "GET /api/notifications*": { unread: 0, items: [] },
  });
  const user = userEvent.setup();
  renderAt("/recent");

  await user.click(await screen.findByRole("button", { name: "Clear history" }));
  const panel = screen.getByRole("region", { name: "Clear history" });
  expect(within(panel).getByText("Remove all finished work from Bevro?")).toBeInTheDocument();
  expect(within(panel).getByText(/Your apps and agents, scheduled work and settings stay/)).toBeInTheDocument();

  await user.click(within(panel).getByRole("button", { name: "Clear history" }));
  expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/tasks")).toBe(true);
});

test("settings can clear the workspace's data without touching its agents", async () => {
  const calls = mockApi({
    "GET /api/meta": { name: "Bevro", version: "0.1.0", tagline: "All your agents and apps in one place.", routing: { mode: "deterministic" } },
    "GET /api/workspaces": [],
    "GET /api/notifications/channels": [{ name: "in_app", label: "In Bevro", available: true, offerable: true, external: false, accepts_destination: false, needs_destination: false, note: null }],
    "GET /api/notifications*": { unread: 0, items: [] },
    "DELETE /api/tasks": { removed: 3 },
    "GET /api/providers": [provider()],
    "GET /api/tasks": [],
  });
  const user = userEvent.setup();
  renderAt("/settings");

  await user.click(await screen.findByRole("button", { name: "Clear task history" }));
  const group = screen.getByRole("group", { name: "Clear task history" });
  await user.click(within(group).getByRole("button", { name: "Clear task history" }));

  expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/tasks")).toBe(true);
  expect(await screen.findByText("Removed 3 tasks.")).toBeInTheDocument();
  // Nothing about providers was touched.
  expect(calls.some((c) => c.method === "DELETE" && c.url.startsWith("/api/providers"))).toBe(false);
});

test("having agents that are merely idle is not an empty workspace", async () => {
  mockApi({
    // Yours, but nothing can run them at this moment.
    "GET /api/providers": [provider({ actions: [], availability: { state: "unavailable", note: "Not available on this installation" } }), notSetUp],
    "GET /api/tasks": [],
    "GET /api/notifications*": { unread: 0, items: [] },
  });
  renderAt("/");

  expect(await screen.findByRole("heading", { name: "What do you want to get done?" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Getting started" })).not.toBeInTheDocument();
});

test("removing an agent says what goes and what stays before asking", async () => {
  const calls = mockApi({
    "GET /api/providers/p1": provider(),
    "GET /api/providers": [provider()],
    "GET /api/providers/p1/details": { id: "p1", active_runtime: null, runtimes: [], source_kind: null },
    "GET /api/providers/p1/removal": { removable: true, history: 4, in_flight: 0, credentials: 1, built_project: true, built_connection: false },
    "DELETE /api/providers/p1": null,
    "GET /api/tasks": [],
    "GET /api/notifications*": { unread: 0, items: [] },
  });
  const user = userEvent.setup();
  renderAt("/apps/p1");

  await user.click(await screen.findByRole("button", { name: "Remove from Bevro" }));

  const question = await screen.findByRole("group", { name: "Remove Support Desk from Bevro?" });
  expect(within(question).getByText("The project Bevro wrote for it is deleted.")).toBeInTheDocument();
  expect(within(question).getByText("Its stored credential is deleted.")).toBeInTheDocument();
  expect(within(question).getByText(/4 tasks stay in Recent, still showing Support Desk/)).toBeInTheDocument();
  expect(within(question).getByText("Nothing outside Bevro is touched: Support Desk itself stays exactly as it is.")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Yes, remove" }));
  expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/providers/p1")).toBe(true);
});

test("an agent busy with something cannot be removed, and says why", async () => {
  mockApi({
    "GET /api/providers/p1": provider(),
    "GET /api/providers": [provider()],
    "GET /api/providers/p1/details": { id: "p1", active_runtime: null, runtimes: [], source_kind: null },
    "GET /api/providers/p1/removal": { removable: true, history: 2, in_flight: 1, credentials: 0, built_project: false, built_connection: false },
    "GET /api/tasks": [],
    "GET /api/notifications*": { unread: 0, items: [] },
  });
  const user = userEvent.setup();
  renderAt("/apps/p1");

  await user.click(await screen.findByRole("button", { name: "Remove from Bevro" }));

  const question = await screen.findByRole("group", { name: "Remove Support Desk from Bevro?" });
  expect(within(question).getByText(/working on something right now/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Yes, remove" })).toBeDisabled();
});

test("work done by an agent that has since gone is still readable, and labelled", async () => {
  const gone = { id: null, slug: "support-desk", name: "Support Desk", removed: true };
  mockApi({
    "GET /api/tasks": [task({ id: "t1", title: "Draft a reply", state: "completed", provider: gone })],
    "GET /api/providers": [],
    "GET /api/notifications*": { unread: 0, items: [] },
  });
  renderAt("/recent");

  expect(await screen.findByText("Draft a reply")).toBeInTheDocument();
  expect(screen.getByText("Support Desk")).toBeInTheDocument();
  expect(screen.getByText("· Removed")).toBeInTheDocument();
});
