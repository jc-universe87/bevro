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
  expect(screen.getByRole("heading", { level: 1, name: "Connect" })).toBeInTheDocument();
  expect(screen.getByLabelText("What is it called, or where is it?")).toBeInTheDocument();
  const input = screen.getByPlaceholderText("A name, a web address, a folder or a command");
  expect(input).toHaveFocus();
  expect(screen.getAllByRole("textbox")).toHaveLength(1);
  expect(screen.queryByText(/capabilit/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Advanced setup" })).toHaveAttribute("href", "/connect/advanced");
});

test("typing a folder finds the agent, adds it, and says where it now is", async () => {
  let polls = 0;
  const calls = mockApi({
    "POST /api/connect/discover": draft({ state: "looking", draft: null }),
    "GET /api/connect/drafts/d1": () => (++polls < 2 ? draft({ state: "looking", draft: null }) : draft()),
    "POST /api/connect/drafts/d1/confirm": provider,
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("A name, a web address, a folder or a command"), "~/agents/market-research{Enter}");
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({ target: "~/agents/market-research", secrets: {} });
  expect(await screen.findByText("Looking for market-research…")).toBeInTheDocument();

  const found = await screen.findByRole("region", { name: "Found" }, { timeout: 3000 });
  expect(within(found).getByRole("heading", { name: "Market Research" })).toBeInTheDocument();
  expect(within(found).getByText("Competitor and market research")).toBeInTheDocument();
  const caps = within(found).getByRole("list", { name: "Capabilities" });
  expect(within(caps).getAllByRole("listitem").map((li) => li.textContent?.replace("• ", ""))).toEqual(["Research", "Product strategy", "Competitor analysis"]);
  expect(within(found).getByRole("heading", { name: "What it can do" })).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/adapter|argv|cwd|\/home\//);
  expect(screen.queryByLabelText(/token|key/i)).not.toBeInTheDocument();

  await user.click(within(found).getByText("How Bevro found this"));
  expect(within(found).getByText(/Runs via:/).parentElement).toHaveTextContent("Runs from this project");

  await user.click(within(found).getByRole("button", { name: "Add to Bevro" }));
  const done = await screen.findByRole("region", { name: "Added to Bevro" });
  expect(within(done).getByText(/Market Research is with your apps and agents\. Bevro can now send it suitable work\./)).toBeInTheDocument();
  expect(within(done).getByRole("link", { name: "Go to Market Research" })).toHaveAttribute("href", "/apps/p9");
  expect(calls.find((c) => c.url === "/api/connect/drafts/d1/confirm")?.body).toEqual({ secrets: {} });
});

test("a credential is added as an explicit step and sent on confirm", async () => {
  const calls = mockApi({
    "POST /api/connect/discover": draft({ target_kind: "url", target_label: "https://sales.example", draft: draftView({ name: "Sales Desk", mechanism_label: "API", availability: "ready", auth: { required: true, secret_name: "api_key", label: "API token", hint: "Sent as a bearer token." } }) }),
    "POST /api/connect/drafts/d1/confirm": { ...provider, name: "Sales Desk" },
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("A name, a web address, a folder or a command"), "https://sales.example{Enter}");
  expect(await screen.findByRole("heading", { name: "Sales Desk needs an API token." })).toBeInTheDocument();
  await user.type(screen.getByLabelText("API token"), "tok-123");
  await user.keyboard("{Enter}");
  const found = screen.getByRole("region", { name: "Found" });
  expect(within(found).getByText("Bevro can work with it directly.")).toBeInTheDocument();
  expect(document.body.textContent).not.toContain("tok-123");
  await user.click(within(found).getByRole("button", { name: "Add to Bevro" }));
  await screen.findByRole("region", { name: "Added to Bevro" });
  expect(calls.find((c) => c.url === "/api/connect/drafts/d1/confirm")?.body).toEqual({ secrets: { api_key: "tok-123" } });
});

test("description Continue saves on Enter, and something described can be added as what it is", async () => {
  const described = draftView({
    name: "Filing Cabinet",
    description: "Search documents and organise files.",
    capabilities: [{ id: "search_documents", title: "Search documents" }, { id: "organise_files", title: "Organise files" }],
    confidence: "low",
    confidence_label: "Needs review",
    note: null,
    needs_description: false,
    described: true,
    can_add: true,
    invocable: false,
    availability: "not_invocable",
    runs_via: "Already running on this machine",
  });
  const calls = mockApi({
    "POST /api/connect/discover": draft({ found_by_name: true, draft: draftView({ name: "Filing Cabinet", description: "", capabilities: [], confidence: "low", confidence_label: "Needs review", note: "Bevro found this, but couldn't tell what it's for.", needs_description: true, invocable: false, availability: "not_invocable" }) }),
    "POST /api/connect/drafts/d1/describe": draft({ found_by_name: true, draft: described }),
    "GET /api/connect/drafts/d1": draft({ found_by_name: true, draft: described }),
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("A name, a web address, a folder or a command"), "filing cabinet{Enter}");
  const field = await screen.findByLabelText("What should Bevro use it for?");
  const continueButton = screen.getByRole("button", { name: "Continue" });
  expect(continueButton).toBeDisabled();
  await user.type(field, "Search documents, organise files{Enter}");
  expect(calls.find((c) => c.url === "/api/connect/drafts/d1/describe")?.body).toEqual({ capability_summary: "Search documents, organise files" });
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByText("Bevro knows what Filing Cabinet is for.")).toBeInTheDocument();
  expect(within(found).getByText("Bevro understands what Filing Cabinet does, but can't send it tasks directly yet.")).toBeInTheDocument();
  expect(within(found).getByRole("button", { name: "Add to Bevro" })).toBeInTheDocument();
  // What was typed is shown once, as a sentence, not again as a list.
  expect(within(found).getByText("Search documents and organise files.")).toBeInTheDocument();
  expect(within(found).queryByRole("list", { name: "Capabilities" })).not.toBeInTheDocument();
  expect(within(found).queryByText("Connected service")).not.toBeInTheDocument();

  // Direct access is the quiet alternative, and it keeps what was said: nothing is typed twice.
  await user.click(within(found).getByRole("button", { name: "Set up direct access" }));
  expect(await screen.findByText(/Setting up Filing Cabinet\./)).toBeInTheDocument();
  expect(screen.getByLabelText("Name")).toHaveValue("Filing Cabinet");
  expect(screen.getByLabelText(/Capabilities/)).toHaveValue("Search documents, Organise files");
});

test("an app with its own website is added as it is, and can be opened straight away", async () => {
  const app = draftView({
    name: "Notebook",
    description: "Keeps what you have noted and learned.",
    capabilities: [{ id: "knowledge", title: "Search what you know" }],
    invocable: false,
    can_add: true,
    availability: "not_invocable",
    runs_via: null,
    runtime: null,
    surfaces: [{ kind: "web_app", role: "use", label: "Web app", sentence: "It has its own web app.", url: "https://notebook.example.net/", reach: "shared" }],
  });
  const added = { ...provider, id: "p5", name: "Notebook", actions: ["open"], direct: { state: "not_set_up" }, surfaces: (app as { surfaces?: unknown }).surfaces };
  const calls = mockApi({ "POST /api/connect/discover": draft({ draft: app }), "POST /api/connect/drafts/d1/confirm": added });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("A name, a web address, a folder or a command"), "notebook{Enter}");
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByRole("heading", { name: "Notebook is available through its own app." })).toBeInTheDocument();
  expect(within(found).getByRole("list", { name: "How you use it" })).toHaveTextContent("It has its own web app.");
  expect(found.textContent).not.toMatch(/only has its own website|almost ready|can't be used|not usable/i);
  await user.click(within(found).getByRole("button", { name: "Add to Bevro" }));
  expect(calls.find((c) => c.url === "/api/connect/drafts/d1/confirm")?.body).toEqual({ secrets: {} });
  const done = await screen.findByRole("region", { name: "Added to Bevro" });
  expect(within(done).getByRole("link", { name: /Open Notebook/ })).toHaveAttribute("href", "https://notebook.example.net/");
  expect(within(done).getByRole("link", { name: "Go to Notebook" })).toHaveAttribute("href", "/apps/p5");
});

test("a failed discovery says why and offers Advanced setup", async () => {
  mockApi({ "POST /api/connect/discover": draft({ state: "failed", draft: null, error: "Nothing answered at that address. Check that it is running and reachable from this machine." }) });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("A name, a web address, a folder or a command"), "http://down.local{Enter}");
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
  await user.type(screen.getByPlaceholderText("A name, a web address, a folder or a command"), "~/agents/notes{Enter}");
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByRole("heading", { name: "Bevro found two ways to connect this. Which should it use?" })).toBeInTheDocument();
  const connect = within(found).getByRole("button", { name: "Add to Bevro" });
  expect(connect).toBeDisabled();
  await user.click(within(found).getByRole("radio", { name: /Uses MCP on this machine/ }));
  await user.click(connect);
  await screen.findByRole("region", { name: "Added to Bevro" });
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
  await user.type(screen.getByPlaceholderText("A name, a web address, a folder or a command"), "~/agents/widget-brain{Enter}");
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByText("Bevro found Widget Brain, but not how you use it.")).toBeInTheDocument();
  expect(within(found).queryByRole("button", { name: "Add to Bevro" })).not.toBeInTheDocument();

  await user.click(within(found).getByRole("button", { name: "Set up direct access" }));
  expect(calls.some((c) => c.url === "/api/connect/drafts/d1/bridge")).toBe(true);
  const progress = await screen.findByRole("region", { name: "Next step" });
  expect(within(progress).getByText("Preparing connection…")).toBeInTheDocument();
  expect(await within(progress).findByText("Building connection", {}, { timeout: 4000 })).toBeInTheDocument();
  expect(await screen.findByText("Added to Bevro", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/bridge|import|stdin|adapter|JSON/i);
});

test("with no agent able to build one, Bevro says what is needed instead of failing quietly", async () => {
  mockApi({
    "POST /api/connect/discover": draft({ draft: draftView({ runs_via: null, runtime: null, invocable: false, needs_bridge: true, bridge_possible: false, invocation_label: null, warnings: ["This project doesn't expose a connection Bevro can use yet."] }) }),
    "GET /api/providers": [],
  });
  const user = userEvent.setup();
  renderAt("/connect");
  await user.type(screen.getByPlaceholderText("A name, a web address, a folder or a command"), "~/agents/widget-brain{Enter}");
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByText("Bevro found Market Research, but not how you use it.")).toBeInTheDocument();
  expect(within(found).getByRole("button", { name: "Set up direct access" })).toBeInTheDocument();
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
  await user.type(screen.getByPlaceholderText("A name, a web address, a folder or a command"), "Market Research{Enter}");

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
  await user.type(screen.getByPlaceholderText("A name, a web address, a folder or a command"), "market-research{Enter}");
  const found = await screen.findByRole("region", { name: "Found" });
  expect(within(found).getByText("Found on this machine")).toBeInTheDocument();
  expect(within(found).getByRole("heading", { name: "Market Research" })).toBeInTheDocument();
});
