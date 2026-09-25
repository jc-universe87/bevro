import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi } from "./helpers";

const preview = (over: Record<string, unknown> = {}) => ({
  name: "Market Watch",
  description: "Tracks competitors and relevant changes in event management software.",
  can: ["Research", "Competitor analysis", "Reports"],
  needs: ["Web access", "Write reports"],
  produces: "a short report",
  schedule: null,
  can_build: true,
  spec: { name: "Market Watch", source: "rules" },
  capabilities: [],
  permissions: [],
  enabled: true,
  ...over,
});

const status = (state: string, steps: string[], over: Record<string, unknown> = {}) => ({
  state,
  note: state === "ready" ? "Created." : state === "failed" ? "Bevro couldn't finish creating this agent." : `${steps[steps.length - 1]}…`,
  provider_id: "p7",
  task_id: "t1",
  steps,
  ...over,
});

function renderAt(path: string) {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

test("create asks one question and shows a preview in plain words", async () => {
  const calls = mockApi({ "POST /api/create/preview": preview(), "GET /api/providers": [] });
  const user = userEvent.setup();
  renderAt("/create");
  expect(screen.getByText("What should this agent do?")).toBeInTheDocument();
  expect(screen.getByPlaceholderText("Describe the work you want it to handle...")).toBeInTheDocument();
  // Nothing technical is asked for up front.
  expect(document.body.textContent).not.toMatch(/language|framework|runtime|adapter|model|repository/i);

  await user.type(screen.getByLabelText("What should this agent do?"), "Research competitors and report weekly");
  await user.click(screen.getByRole("button", { name: "Create" }));
  const section = await screen.findByRole("region", { name: "Preview" });
  expect(within(section).getByRole("heading", { name: "Market Watch" })).toBeInTheDocument();
  expect(within(section).getByRole("list", { name: "Can" }).textContent).toContain("Research");
  expect(within(section).getByRole("list", { name: "Needs" }).textContent).toContain("Web access");
  expect(within(section).getByText(/a short report/)).toBeInTheDocument();
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({ description: "Research competitors and report weekly" });
  expect(document.body.textContent).not.toMatch(/spec|json|capabilit(y|ies)_id|permission/i);
});

test("creating an agent shows calm steps and then the finished agent", async () => {
  let polls = 0;
  const calls = mockApi({
    "POST /api/create/preview": preview(),
    "POST /api/create/build": status("designing", ["Designing"]),
    "GET /api/create/builds/p7": () =>
      ++polls < 2 ? status("building", ["Designing", "Building"]) : status("ready", ["Designing", "Building", "Testing", "Connecting"]),
    "GET /api/providers": [],
    "GET /api/tasks": [],
  });
  const user = userEvent.setup();
  renderAt("/create");
  await user.type(screen.getByLabelText("What should this agent do?"), "Research competitors and report weekly");
  await user.click(screen.getByRole("button", { name: "Create" }));
  await user.click(await screen.findByRole("button", { name: "Create agent" }));

  const building = await screen.findByRole("region", { name: "Creating" });
  expect(within(building).getByText(/Preparing agent…/)).toBeInTheDocument();
  expect(await within(building).findByText("Building", {}, { timeout: 4000 })).toBeInTheDocument();
  expect(await screen.findByText("Created", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ask" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "View agent" })).toHaveAttribute("href", "/apps/p7");
  expect(calls.find((c) => c.url === "/api/create/build")?.body).toEqual({ spec: { name: "Market Watch", source: "rules" }, description: "Research competitors and report weekly" });
  expect(document.body.textContent).not.toMatch(/python|pytest|pyproject|discovery|runtime|adapter/i);
}, 15000);

test("with no coding agent connected, Create says so and offers to connect one", async () => {
  mockApi({ "POST /api/create/preview": preview({ can_build: false }), "GET /api/providers": [] });
  const user = userEvent.setup();
  renderAt("/create");
  await user.type(screen.getByLabelText("What should this agent do?"), "Research competitors");
  await user.click(screen.getByRole("button", { name: "Create" }));
  const section = await screen.findByRole("region", { name: "Preview" });
  expect(within(section).getByText("Creating agents needs a coding agent in your apps and agents.")).toBeInTheDocument();
  expect(within(section).getByRole("link", { name: "Connect coding agent" })).toHaveAttribute("href", "/connect");
  expect(within(section).queryByRole("button", { name: "Create agent" })).not.toBeInTheDocument();
});

test("an agent Bevro created lets its purpose be edited on its own page", async () => {
  const provider = {
    id: "p7", slug: "market-watch", name: "Market Watch", description: "Tracks competitors.", enabled: true, capabilities: [{ id: "research", title: "Research" }],
    app_url: null, icon: null, origin: "created", actions: ["ask"], connection: "command",
    availability: { state: "available", note: null }, secret_names: [], credentials: [],
    runtime: { display_name: "Runs from this project", availability: "needs_worker", credentials_label: "None needed", runtimes_found: 1, alternatives: 0, health: "available", built: false, review: null, abilities: {} },
    build: { purpose: "Tracks competitors in event management software.", version: 2, state: "ready", built_by: "fixture-agent-builder", needs: ["web"], can_rebuild: true },
    created_at: "", updated_at: "",
  };
  const calls = mockApi({
    "GET /api/providers/p7": provider,
    "POST /api/create/builds/p7/rebuild": status("designing", ["Designing"]),
  });
  const user = userEvent.setup();
  renderAt("/apps/p7");
  const panel = await screen.findByRole("region", { name: "What it can do" });
  expect(within(panel).getByText("Tracks competitors in event management software.", { exact: false })).toBeInTheDocument();
  await user.click(within(panel).getByRole("button", { name: "Edit purpose" }));
  const field = within(panel).getByLabelText("What should this agent do?");
  await user.clear(field);
  await user.type(field, "Track competitors and summarise each week.");
  await user.click(within(panel).getByRole("button", { name: "Save and rebuild" }));
  expect(calls.find((c) => c.url === "/api/create/builds/p7/rebuild")?.body).toEqual({ description: "Track competitors and summarise each week." });
  expect(await screen.findByText(/keeps working until it passes/)).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/python|pyproject|source code|adapter/i);
});
