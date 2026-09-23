import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi } from "./helpers";

const draftView = (over: Record<string, unknown> = {}) => ({
  runs_via: "Runs from this project",
  runtime: { id: "cli", display_name: "Runs from this project", availability: "needs_worker", confidence: "high", credentials: { label: "Missing", required_from_user: false, names: [], note: null }, abilities: {}, evidence: [], warnings: [], invocable: true },
  runtime_options: [],
  runtimes_found: 1,
  choice_needed: false,
  credentials_label: "None needed",
  needs_bridge: false,
  bridge_possible: false,
  name: "Market Research",
  description: "Competitor and market research",
  capabilities: [{ id: "research", title: "Research" }, { id: "product_strategy", title: "Product strategy" }, { id: "competitor_analysis", title: "Competitor analysis" }],
  mechanism: "command",
  mechanism_label: "Local Python agent",
  invocation_label: 'python -m market_research.agent --topic "…"',
  availability: "needs_worker",
  confidence: "high",
  confidence_label: "Confident",
  note: null,
  evidence: ["pyproject.toml declares market-research 1.4.1", "README documents python -m market_research.agent"],
  warnings: [],
  app_url: null,
  auth: { required: false, secret_name: null, label: null, hint: null },
  invocable: true,
  ...over,
});

const draft = (over: Record<string, unknown> = {}) => ({
  id: "d1",
  state: "found",
  target_kind: "local",
  target_label: "market-research",
  draft: draftView(),
  error: null,
  test: null,
  provider_id: null,
  created_at: "",
  ...over,
});

const provider = { id: "p9", slug: "market-research", name: "Market Research", description: "Competitor and market research", enabled: true, capabilities: [], app_url: null, icon: null, origin: "connected", actions: [], connection: "command", availability: { state: "unavailable", note: "Not available on this installation" }, secret_names: [], credentials: [], runtime: { display_name: "Runs from this project", availability: "needs_worker", credentials_label: "None needed", runtimes_found: 1, alternatives: 0, health: "available", built: false, review: null, abilities: {} }, created_at: "", updated_at: "" };

function renderAt(path: string) {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

test("connect shows one field and no technical form", () => {
  mockApi({ "GET /api/providers": [] });
  renderAt("/connect");
  expect(screen.getByText("Connect an agent, app or service to Bevro.")).toBeInTheDocument();
  const input = screen.getByPlaceholderText("URL, local project, MCP server or command");
  expect(input).toHaveFocus();
  expect(screen.getAllByRole("textbox")).toHaveLength(1);
  expect(screen.queryByText(/capabilit/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Advanced setup" })).toHaveAttribute("href", "/connect/advanced");
});

test("typing a folder finds the agent, confirms it, and says it is under Agents", async () => {
  let polls = 0;
  const calls = mockApi({
    "POST /api/connect/discover": draft({ state: "looking", draft: null }),
    "GET /api/connect/drafts/d1": () => (++polls < 2 ? draft({ state: "looking", draft: null }) : draft()),
    "POST /api/connect/drafts/d1/confirm": provider,
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("URL, local project, MCP server or command"), "~/agents/market-research{Enter}");
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({ target: "~/agents/market-research", secrets: {} });
  expect(await screen.findByText("Looking at market-research…")).toBeInTheDocument();

  const found = await screen.findByRole("region", { name: "Found" }, { timeout: 3000 });
  expect(within(found).getByRole("heading", { name: "Market Research" })).toBeInTheDocument();
  expect(within(found).getByText("Competitor and market research")).toBeInTheDocument();
  const caps = within(found).getByRole("list", { name: "Capabilities" });
  expect(within(caps).getAllByRole("listitem").map((li) => li.textContent?.replace("• ", ""))).toEqual(["Research", "Product strategy", "Competitor analysis"]);
  expect(within(found).getByText("Runs via:").parentElement).toHaveTextContent("Runs from this project");
  expect(within(found).getByText("Can:")).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/adapter|argv|cwd|\/home\//);
  expect(screen.queryByLabelText(/token|key/i)).not.toBeInTheDocument();

  await user.click(within(found).getByRole("button", { name: "Connect" }));
  const done = await screen.findByRole("region", { name: "Connected" });
  expect(within(done).getByText(/Market Research is available under Agents\. Bevro can now route suitable work here\./)).toBeInTheDocument();
  expect(within(done).getByRole("link", { name: "Go to Agents" })).toHaveAttribute("href", "/agents");
  expect(calls.find((c) => c.url === "/api/connect/drafts/d1/confirm")?.body).toEqual({ secrets: {} });
});

test("authentication is asked for only when needed and sent on confirm", async () => {
  const calls = mockApi({
    "POST /api/connect/discover": draft({ target_kind: "url", target_label: "https://sales.example", draft: draftView({ name: "Sales Desk", mechanism_label: "API", availability: "ready", auth: { required: true, secret_name: "api_key", label: "API token", hint: "Sent as a bearer token." } }) }),
    "POST /api/connect/drafts/d1/confirm": { ...provider, name: "Sales Desk" },
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("URL, local project, MCP server or command"), "https://sales.example{Enter}");
  expect(await screen.findByText("Authentication required")).toBeInTheDocument();
  await user.type(screen.getByLabelText("API token"), "tok-123");
  await user.click(within(screen.getByRole("region", { name: "Found" })).getByRole("button", { name: "Connect" }));
  await screen.findByRole("region", { name: "Connected" });
  expect(calls.find((c) => c.url === "/api/connect/drafts/d1/confirm")?.body).toEqual({ secrets: { api_key: "tok-123" } });
});

test("when unsure, the person edits a plain capability summary before connecting", async () => {
  const calls = mockApi({
    "POST /api/connect/discover": draft({ draft: draftView({ name: "Bare Service", capabilities: [], confidence: "low", confidence_label: "Needs review", note: "I found this provider but I'm not fully sure what it can do.", mechanism_label: "API", availability: "ready" }) }),
    "POST /api/connect/drafts/d1/confirm": { ...provider, name: "Bare Service" },
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("URL, local project, MCP server or command"), "http://bare.local{Enter}");
  expect(await screen.findByText("I found this provider but I'm not fully sure what it can do.")).toBeInTheDocument();
  await user.type(screen.getByLabelText("What can it do?"), "Quotes, Bookings");
  await user.click(within(screen.getByRole("region", { name: "Found" })).getByRole("button", { name: "Connect" }));
  await screen.findByRole("region", { name: "Connected" });
  expect(calls.find((c) => c.url === "/api/connect/drafts/d1/confirm")?.body).toEqual({ secrets: {}, capability_summary: "Quotes, Bookings" });
});

test("a failed discovery says why and offers Advanced setup", async () => {
  mockApi({ "POST /api/connect/discover": draft({ state: "failed", draft: null, error: "Nothing answered at that address. Check that it is running and reachable from this machine." }) });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("URL, local project, MCP server or command"), "http://down.local{Enter}");
  expect(await screen.findByRole("alert")).toHaveTextContent("Nothing answered at that address.");
  expect(screen.getAllByRole("link", { name: "Advanced setup" }).length).toBeGreaterThan(0);
});

test("advanced setup posts the technical fields to the providers endpoint", async () => {
  const calls = mockApi({ "POST /api/providers": provider, "GET /api/providers": [provider] });
  const user = userEvent.setup();
  renderAt("/connect/advanced");
  await user.click(screen.getByRole("radio", { name: "Command" }));
  await user.type(screen.getByLabelText("Name"), "Runner");
  await user.type(screen.getByPlaceholderText("python -m my_agent"), "python -m runner");
  await user.type(screen.getByLabelText(/How the request is passed/), "--task");
  await user.click(screen.getByRole("button", { name: "Connect" }));
  const post = calls.find((c) => c.method === "POST");
  expect(post?.body).toEqual({ name: "Runner", description: "", capabilities: [], method: "command", details: { command: "python -m runner", input_flag: "--task" }, secrets: {}, app_url: null });
});

test("agents lists a connected provider and Manage offers pause, test and remove without technical details", async () => {
  const calls = mockApi({
    "GET /api/providers": [{ ...provider, actions: ["ask"], availability: { state: "available", note: null } }],
    "POST /api/providers/p9/check": { ok: true, detail: null },
    "DELETE /api/providers/p9": {},
  });
  const user = userEvent.setup();
  renderAt("/agents");
  expect(await screen.findByRole("heading", { name: "Market Research" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Manage" }));
  const panel = screen.getByLabelText("Manage Market Research");
  expect(within(panel).getByText("Runs from this project")).toBeInTheDocument();
  expect(within(panel).getByRole("button", { name: "Reconnect" })).toBeInTheDocument();
  expect(panel.textContent).not.toMatch(/argv|cwd|python -m|stdio|systemd/);
  await user.click(within(panel).getByRole("button", { name: "Test" }));
  expect(await within(panel).findByText("Reachable.")).toBeInTheDocument();
  await user.click(within(panel).getByRole("button", { name: "Remove" }));
  await user.click(within(panel).getByRole("button", { name: "Yes, remove" }));
  expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/providers/p9")).toBe(true);
  expect(screen.queryByRole("heading", { name: "Market Research" })).not.toBeInTheDocument();
});

test("when two ways are close, the person picks one and the choice is sent", async () => {
  const options = [
    { id: "mcp", display_name: "Uses MCP on this machine", availability: "needs_worker", confidence: "medium", credentials: { label: "None needed", required_from_user: false, names: [], note: null }, abilities: {}, evidence: [], warnings: [], invocable: true },
    { id: "cli", display_name: "Runs from this project", availability: "needs_worker", confidence: "high", credentials: { label: "Missing", required_from_user: true, names: ["OPENAI_API_KEY"], note: null }, abilities: {}, evidence: [], warnings: [], invocable: true },
  ];
  const calls = mockApi({
    "POST /api/connect/discover": draft({ draft: draftView({ choice_needed: true, runtime_options: options, runtimes_found: 2 }) }),
    "POST /api/connect/drafts/d1/confirm": provider,
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("URL, local project, MCP server or command"), "~/agents/notes{Enter}");
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByText("I found two ways to connect this. Which should Bevro use?")).toBeInTheDocument();
  const connect = within(found).getByRole("button", { name: "Connect" });
  expect(connect).toBeDisabled();
  await user.click(within(found).getByRole("radio", { name: /Uses MCP on this machine/ }));
  await user.click(connect);
  await screen.findByRole("region", { name: "Connected" });
  expect(calls.find((c) => c.url === "/api/connect/drafts/d1/confirm")?.body).toEqual({ secrets: {}, runtime_id: "mcp" });
});

test("a project with no way in offers to have a connection built, and follows it in plain words", async () => {
  const noWayIn = draftView({
    name: "Widget Brain", description: "Answers questions about the widget market",
    runs_via: null, runtime: null, invocable: false, needs_bridge: true, bridge_possible: true,
    invocation_label: null, evidence: ["pyproject.toml declares widget-brain 0.3.0"],
    warnings: ["This project doesn't expose a connection Bevro can use yet."],
  });
  let polls = 0;
  const calls = mockApi({
    "POST /api/connect/discover": draft({ draft: noWayIn }),
    "POST /api/connect/drafts/d1/bridge": { state: "preparing", note: "Preparing connection…", provider_id: "p42", task_id: null, steps: ["Inspecting project"] },
    "GET /api/connect/bridges/p42": () =>
      ++polls < 2
        ? { state: "building", note: "Building connection…", provider_id: "p42", task_id: "t1", steps: ["Inspecting project", "Building connection"] }
        : { state: "ready", note: "Runs through a connection Bevro built", provider_id: "p42", task_id: "t1", steps: ["Inspecting project", "Building connection", "Testing connection"] },
    "GET /api/providers": [],
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("URL, local project, MCP server or command"), "~/agents/widget-brain{Enter}");
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByText("This project doesn't expose a connection Bevro can use yet.")).toBeInTheDocument();
  expect(within(found).queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();

  await user.click(within(found).getByRole("button", { name: "Make it connectable" }));
  expect(calls.some((c) => c.url === "/api/connect/drafts/d1/bridge")).toBe(true);
  const progress = await screen.findByRole("region", { name: "Preparing connection" });
  expect(within(progress).getByText("Preparing connection…")).toBeInTheDocument();
  expect(await within(progress).findByText("Building connection", {}, { timeout: 4000 })).toBeInTheDocument();
  expect(await screen.findByText("Connected", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/bridge|import|stdin|adapter|JSON/i);
});

test("with no agent able to build one, Bevro says what is needed instead of failing quietly", async () => {
  mockApi({
    "POST /api/connect/discover": draft({ draft: draftView({ runs_via: null, runtime: null, invocable: false, needs_bridge: true, bridge_possible: false, invocation_label: null, warnings: ["This project doesn't expose a connection Bevro can use yet."] }) }),
    "GET /api/providers": [],
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("URL, local project, MCP server or command"), "~/agents/widget-brain{Enter}");
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByRole("link", { name: "Connect a coding agent" })).toHaveAttribute("href", "/agents");
  expect(within(found).getAllByRole("link", { name: "Advanced setup" }).length).toBeGreaterThan(0);
});
