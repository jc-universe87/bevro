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
  needs_description: false,
  described: false,
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
  const input = screen.getByPlaceholderText("Paste an address, folder, command, or name");
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
  await user.type(screen.getByPlaceholderText("Paste an address, folder, command, or name"), "~/agents/market-research{Enter}");
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({ target: "~/agents/market-research", secrets: {} });
  expect(await screen.findByText("Looking for market-research…")).toBeInTheDocument();

  const found = await screen.findByRole("region", { name: "Found" }, { timeout: 3000 });
  expect(within(found).getByRole("heading", { name: "Market Research" })).toBeInTheDocument();
  expect(within(found).getByText("Competitor and market research")).toBeInTheDocument();
  const caps = within(found).getByRole("list", { name: "Capabilities" });
  expect(within(caps).getAllByRole("listitem").map((li) => li.textContent?.replace("• ", ""))).toEqual(["Research", "Product strategy", "Competitor analysis"]);
  expect(within(found).getByText("Can:")).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/adapter|argv|cwd|\/home\//);
  expect(screen.queryByLabelText(/token|key/i)).not.toBeInTheDocument();

  await user.click(within(found).getByText("How Bevro found this"));
  expect(within(found).getByText(/Runs via:/).parentElement).toHaveTextContent("Runs from this project");

  await user.click(within(found).getByRole("button", { name: "Connect" }));
  const done = await screen.findByRole("region", { name: "Connected" });
  expect(within(done).getByText(/Market Research is available under Agents\. Bevro can now route suitable work here\./)).toBeInTheDocument();
  expect(within(done).getByRole("link", { name: "Go to Agents" })).toHaveAttribute("href", "/agents");
  expect(calls.find((c) => c.url === "/api/connect/drafts/d1/confirm")?.body).toEqual({ secrets: {} });
});

test("a credential is added as an explicit step and sent on confirm", async () => {
  const calls = mockApi({
    "POST /api/connect/discover": draft({ target_kind: "url", target_label: "https://sales.example", draft: draftView({ name: "Sales Desk", mechanism_label: "API", availability: "ready", auth: { required: true, secret_name: "api_key", label: "API token", hint: "Sent as a bearer token." } }) }),
    "POST /api/connect/drafts/d1/confirm": { ...provider, name: "Sales Desk" },
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("Paste an address, folder, command, or name"), "https://sales.example{Enter}");
  expect(await screen.findByRole("heading", { name: "Sales Desk needs an API token." })).toBeInTheDocument();
  await user.type(screen.getByLabelText("API token"), "tok-123");
  await user.keyboard("{Enter}");
  const found = screen.getByRole("region", { name: "Found" });
  expect(within(found).getByText("Ready to connect.")).toBeInTheDocument();
  expect(document.body.textContent).not.toContain("tok-123");
  await user.click(within(found).getByRole("button", { name: "Connect" }));
  await screen.findByRole("region", { name: "Connected" });
  expect(calls.find((c) => c.url === "/api/connect/drafts/d1/confirm")?.body).toEqual({ secrets: { api_key: "tok-123" } });
});

test("description Continue saves on Enter and advances to the next unresolved step", async () => {
  const described = draftView({
    name: "Archivist",
    description: "Search documents and organise files.",
    capabilities: [{ id: "search_documents", title: "Search documents" }, { id: "organise_files", title: "Organise files" }],
    confidence: "low",
    confidence_label: "Needs review",
    note: null,
    needs_description: false,
    described: true,
    invocable: false,
    availability: "not_invocable",
    runs_via: "Already running on this machine",
  });
  const calls = mockApi({
    "POST /api/connect/discover": draft({ found_by_name: true, draft: draftView({ name: "Archivist", description: "", capabilities: [], confidence: "low", confidence_label: "Needs review", note: "Bevro found this, but couldn't tell what it's for.", needs_description: true, invocable: false, availability: "not_invocable" }) }),
    "POST /api/connect/drafts/d1/describe": draft({ found_by_name: true, draft: described }),
    "GET /api/connect/drafts/d1": draft({ found_by_name: true, draft: described }),
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("Paste an address, folder, command, or name"), "archivist{Enter}");
  const field = await screen.findByLabelText("What should Bevro use it for?");
  const continueButton = screen.getByRole("button", { name: "Continue" });
  expect(continueButton).toBeDisabled();
  await user.type(field, "Search documents, organise files{Enter}");
  expect(calls.find((c) => c.url === "/api/connect/drafts/d1/describe")?.body).toEqual({ capability_summary: "Search documents, organise files" });
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByText("Archivist is almost ready.")).toBeInTheDocument();
  expect(within(found).getByText("Bevro found it, but not yet a way to send it a task.")).toBeInTheDocument();
  expect(within(found).getByRole("button", { name: "Set up how to use it" })).toBeInTheDocument();
  // What was typed is shown once, as a sentence, not again as a list.
  expect(within(found).getByText("Search documents and organise files.")).toBeInTheDocument();
  expect(within(found).queryByRole("list", { name: "Capabilities" })).not.toBeInTheDocument();
  expect(within(found).queryByText("Connected service")).not.toBeInTheDocument();

  // The way forward keeps what was said: nothing is typed twice.
  await user.click(within(found).getByRole("button", { name: "Set up how to use it" }));
  expect(await screen.findByText(/Setting up Archivist\./)).toBeInTheDocument();
  expect(screen.getByLabelText("Name")).toHaveValue("Archivist");
  expect(screen.getByLabelText(/Capabilities/)).toHaveValue("Search documents, Organise files");
});

test("a failed discovery says why and offers Advanced setup", async () => {
  mockApi({ "POST /api/connect/discover": draft({ state: "failed", draft: null, error: "Nothing answered at that address. Check that it is running and reachable from this machine." }) });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("Paste an address, folder, command, or name"), "http://down.local{Enter}");
  const next = await screen.findByRole("region", { name: "Next step" });
  expect(within(next).getByText(/Nothing answered at that address/)).toBeInTheDocument();
  expect(within(next).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  expect(within(next).getByRole("button", { name: "Set it up by hand" })).toBeInTheDocument();
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
    "POST /api/providers/p9/check": { ok: true, detail: "Everything Bevro can check is in place.", checks: [{ label: "Bevro can reach it", ok: true }, { label: "No real task was run", ok: null, kind: "functional" }] },
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
  const results = await within(panel).findByRole("region", { name: "Test results" });
  expect(within(results).getByText("Test passed.")).toBeInTheDocument();
  expect(within(results).getByText("Bevro can reach it")).toBeInTheDocument();
  await user.click(within(panel).getByRole("button", { name: "Remove" }));
  await user.click(within(panel).getByRole("button", { name: "Yes, remove" }));
  expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/providers/p9")).toBe(true);
  expect(screen.queryByRole("heading", { name: "Market Research" })).not.toBeInTheDocument();
});

test("Manage leads with what is in the way and the one thing that fixes it", async () => {
  const noWayIn = { ...provider, availability: { state: "unavailable", note: "No way in that Bevro can use", reason: "nothing_usable" } };
  const noWorker = { ...provider, id: "p8", slug: "notes", name: "Notes", availability: { state: "unavailable", note: "Waiting for the worker on this machine", reason: "waiting_for_worker" } };
  const noKey = {
    ...provider,
    id: "p7",
    slug: "moimio",
    name: "Moimio Research",
    availability: { state: "unavailable", note: "Needs a credential", reason: "needs_credential" },
    credentials: [{ name: "OPENAI_API_KEY", label: "OpenAI API key", present: false, source: "missing", status: "Missing", note: "Moimio Research already has a credential for its scheduled runs, but that credential isn't available when Bevro starts a new task.", why: "The credential is handed over only when its scheduled service starts." }],
  };
  const calls = mockApi({
    "GET /api/providers": [noWayIn, noWorker, noKey],
    "POST /api/providers/p8/check": { ok: false, detail: "Bevro can't reach this machine right now.", checks: [{ label: "Bevro can't reach it right now", ok: false }] },
  });
  const user = userEvent.setup();
  renderAt("/agents");
  await screen.findByRole("heading", { name: "Market Research" });
  const open = (name: string) => user.click(within(screen.getByRole("heading", { name }).closest("li")!).getByRole("button", { name: "Manage" }));

  await open("Market Research");
  const setup = within(screen.getByLabelText("Manage Market Research")).getByRole("region", { name: "Needs attention" });
  expect(within(setup).getByRole("heading", { name: "Needs setup" })).toBeInTheDocument();
  expect(setup.textContent).toMatch(/doesn't yet know how to send it work/);
  const setupButton = within(setup).getByRole("button", { name: "Set up how to use it" });
  // The blocker's action comes before the maintenance controls.
  const panel = screen.getByLabelText("Manage Market Research");
  const buttons = within(panel).getAllByRole("button").map((b) => b.textContent);
  expect(buttons.indexOf(setupButton.textContent)).toBeLessThan(buttons.indexOf("Test"));
  await user.click(within(setup).getByRole("button", { name: /Why can't Bevro use it yet/ }));
  expect(within(setup).getByRole("note")).toBeVisible();

  await open("Notes");
  const worker = within(screen.getByLabelText("Manage Notes")).getByRole("region", { name: "Needs attention" });
  expect(within(worker).getByRole("heading", { name: "Bevro can't reach this machine right now." })).toBeInTheDocument();
  await user.click(within(worker).getByRole("button", { name: "Try again" }));
  expect(calls.some((c) => c.method === "POST" && c.url === "/api/providers/p8/check")).toBe(true);

  await open("Moimio Research");
  const key = within(screen.getByLabelText("Manage Moimio Research")).getByRole("region", { name: "Needs attention" });
  expect(within(key).getByRole("heading", { name: "Needs a credential" })).toBeInTheDocument();
  expect(within(key).getByRole("button", { name: "Add credential" })).toBeInTheDocument();
  expect(within(key).getByRole("button", { name: "Why can't Bevro use the existing one?" })).toBeInTheDocument();
  expect(key.textContent?.match(/already has a credential for its scheduled runs/g)).toHaveLength(1);
  expect(key.textContent).not.toMatch(/systemd|EnvironmentFile/);
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
  await user.type(screen.getByPlaceholderText("Paste an address, folder, command, or name"), "~/agents/notes{Enter}");
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByRole("heading", { name: "Bevro found two ways to connect this. Which should it use?" })).toBeInTheDocument();
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
  await user.type(screen.getByPlaceholderText("Paste an address, folder, command, or name"), "~/agents/widget-brain{Enter}");
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByText("Bevro found it, but not yet a way to send it a task.")).toBeInTheDocument();
  expect(within(found).queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();

  await user.click(within(found).getByRole("button", { name: "Set up how to use it" }));
  expect(calls.some((c) => c.url === "/api/connect/drafts/d1/bridge")).toBe(true);
  const progress = await screen.findByRole("region", { name: "Next step" });
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
  await user.type(screen.getByPlaceholderText("Paste an address, folder, command, or name"), "~/agents/widget-brain{Enter}");
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByText("Bevro found it, but not yet a way to send it a task.")).toBeInTheDocument();
  expect(within(found).getByRole("button", { name: "Set up how to use it" })).toBeInTheDocument();
});

test("a name that fits two folders asks which one, and sends only the choice", async () => {
  const calls = mockApi({
    "POST /api/connect/discover": draft({
      state: "choice_required",
      target_kind: "name",
      target_label: "Market Research",
      draft: null,
      choices: [
        { label: "market-research", where: "~/agents/market-research" },
        { label: "market_research", where: "~/archive/market_research" },
      ],
    }),
    "POST /api/connect/drafts/d1/choose": draft({
      state: "trust_required",
      draft: null,
      trust: { kind: "folder", label: "market_research", path: "/home/someone/archive/market_research", exists: true },
    }),
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("Paste an address, folder, command, or name"), "Market Research{Enter}");

  const which = await screen.findByRole("region", { name: "Next step" });
  expect(within(which).getByText("Found a few matches on this machine.")).toBeInTheDocument();
  expect(within(which).getByText("~/archive/market_research")).toBeInTheDocument();
  await user.click(within(which).getAllByRole("button", { name: /^Choose / })[1]);

  expect(calls.find((c) => c.url === "/api/connect/drafts/d1/choose")?.body).toEqual({ choice: 1 });
  expect(await screen.findByRole("button", { name: "Allow folder" })).toBeInTheDocument();
});

test("something found by its name says it was found on this machine", async () => {
  mockApi({ "POST /api/connect/discover": draft({ found_by_name: true, target_label: "market-research" }) });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("Paste an address, folder, command, or name"), "market-research{Enter}");
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByText("Found on this machine")).toBeInTheDocument();
  expect(within(found).getByRole("heading", { name: "Market Research" })).toBeInTheDocument();
});
