/**
 * What Apps & agents shows about a connected thing.
 *
 * A service's own description is written for whoever integrates with it. It
 * is kept, and it belongs behind Advanced details - not on the card, where
 * someone is trying to see at a glance what they have.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi } from "./helpers";

const INTEGRATION_PROSE =
  "Thin control API over the Example modules. It exposes existing capabilities (profiles, strategies, runs, " +
  "decisions, preparation, review, tracking, artifacts, statistics); it implements no search, evaluation, " +
  "matching, wording, approval or state-transition logic of its own. GET routes read. POST routes are explicit " +
  "operations. No authentication, no database.";

const service = {
  id: "p1",
  slug: "example-service",
  name: "Example Service",
  description: "Reviews applications and opportunities, and prepares documents.",
  details: {
    what_it_does: "Reviews applications and opportunities, prepares documents, and reports on statistics.",
    how_it_connects:
      "Bevro connects to it directly and uses the features it already has. It keeps its own data and its own way of working; Bevro sends it work and brings the results back here.",
  },
  enabled: true,
  capabilities: [
    { id: "documents", title: "Documents" },
    { id: "applications", title: "Applications" },
  ],
  app_url: null,
  icon: { kind: "letter", text: "E" },
  origin: "connected",
  actions: ["ask"],
  connection: "api",
  availability: { state: "available", note: null },
  secret_names: [],
  credentials: [],
  runtime: { display_name: "Connected over the network", runs_at: "On this machine", availability: "ready", credentials_label: "None needed", runtimes_found: 1, alternatives: 0, health: "available", abilities: {} },
  created_at: "",
  updated_at: "",
};

const details = {
  id: "p1",
  active_runtime: { id: "openapi", kind: "openapi", adapter: "openapi", display_name: "Connected over the network", credential_strategy: "none" },
  runtimes: [{ id: "openapi", kind: "openapi", adapter: "openapi", display_name: "Connected over the network", availability: "ready", active: true, credential_strategy: "none" }],
  source_kind: "url",
  source_target: "http://service.local/p/someone/",
  source_description: INTEGRATION_PROSE,
  source_name: "Example Service control API",
  operation_count: 68,
  runs_at: "This machine",
  reachability: { api: "unavailable", worker: "available" },
};

function renderAt(path: string) {
  mockApi({ "GET /api/providers": [service], "GET /api/providers/p1": service, "GET /api/providers/p1/details": details });
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

test("the card shows one short sentence, not the service's own account of its interface", async () => {
  renderAt("/apps");
  const list = await screen.findByRole("list", { name: "Apps and agents" });
  expect(within(list).getByText(service.description)).toBeInTheDocument();
  expect(within(list).queryByText(/Thin control API/)).not.toBeInTheDocument();
  expect(list.textContent).not.toMatch(/GET |POST |OpenAPI|endpoint/);
  // Name, status, summary and the actions - and nothing else.
  expect(within(list).getByRole("heading", { name: "Example Service" })).toBeInTheDocument();
  expect(list.textContent).not.toContain("control API");
  expect(within(list).getByRole("button", { name: "Use in Bevro" })).toBeInTheDocument();
});

test("its page explains what it does and how it is used, in plain English", async () => {
  renderAt("/apps/p1");
  const can = await screen.findByRole("region", { name: "What it can do" });
  expect(within(can).getByText(/Reviews applications and opportunities, prepares documents, and reports on statistics\./)).toBeInTheDocument();
  const abilities = within(can).getByRole("list", { name: "Capabilities" });
  expect(within(abilities).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Documents", "Applications"]);

  const use = screen.getByRole("region", { name: "How you can use it" });
  expect(use).toHaveTextContent("Ask Bevro for it, from Home or here. Bevro sends it the work and brings the result back.");
  // None of the service's own account of its interface outside Advanced details.
  expect(document.body.textContent).not.toMatch(/Thin control API|GET |POST |OpenAPI|endpoint/);
});

test("the technical account is behind Advanced details, with the original text", async () => {
  renderAt("/apps/p1");
  const panel = await screen.findByRole("region", { name: "Settings" });
  expect(document.body.textContent).not.toMatch(/Thin control API/);
  await userEvent.click(within(panel).getByText("Advanced details"));

  expect(await within(panel).findByText("http://service.local/p/someone/")).toBeInTheDocument();
  // The exact name the service gives itself, kept where a technical fact belongs.
  expect(within(panel).getByText("Example Service control API")).toBeInTheDocument();
  expect(within(panel).getByText("68")).toBeInTheDocument();
  expect(within(panel).getByText("This machine")).toBeInTheDocument();
  expect(within(panel).getByText(/Bevro itself: unavailable/)).toBeInTheDocument();
  expect(within(panel).getByText(/Thin control API/)).toBeInTheDocument();
  // How it connects, still in plain English.
  expect(within(panel).getByText("How it connects")).toBeInTheDocument();
  expect(within(panel).getByText(/keeps its own data/)).toBeInTheDocument();
});
