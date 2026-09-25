/**
 * An installed service that has the key, and why it doesn't help.
 *
 * The card and Manage stay as simple as ever: the thing needs a credential.
 * Advanced details is where it says what Bevro found - the service that has
 * one, that it can't be handed work, that starting it needs an administrator -
 * so that "needs a credential" never looks like Bevro didn't notice.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi } from "./helpers";

const project = {
  id: "p2",
  slug: "brief-writer",
  name: "Brief Writer",
  description: "Writes briefs about widgets.",
  details: { what_it_does: "Writes briefs about widgets.", how_it_connects: "Bevro runs it on this machine." },
  enabled: true,
  capabilities: [{ id: "research", title: "Research" }],
  app_url: null,
  icon: { kind: "letter", text: "B" },
  origin: "connected",
  actions: ["ask"],
  connection: "needs_credential",
  availability: { state: "unavailable", note: "Needs a credential" },
  secret_names: [],
  credentials: [],
  runtime: { display_name: "Runs from this project", availability: "needs_worker", credentials_label: "Missing", runtimes_found: 2, alternatives: 0, health: "unknown", abilities: {} },
  created_at: "",
  updated_at: "",
};

const details = {
  id: "p2",
  active_runtime: null,
  runtimes: [
    { id: "cli", kind: "python_entrypoint", adapter: "command", display_name: "Runs from this project", availability: "needs_worker", active: true, credential_strategy: "bevro_managed" },
    { id: "systemd", kind: "systemd", adapter: "", display_name: "Runs as a local service (systemd)", availability: "not_invocable", active: false, credential_strategy: "systemd_environment_file", usable: false },
  ],
  source_kind: "local",
  contexts: [
    {
      id: "systemd",
      title: "Installed system service",
      name: "brief-writer.service",
      credentials: "Provides OPENAI_API_KEY",
      takes_work: "No",
      needs_admin: "Yes",
      authorised: "No",
      available: true,
      why_not: ["It runs a fixed job and has no way to be given a request.", "Using it needs administrator permission."],
    },
  ],
};

function agents() {
  mockApi({ "GET /api/providers": [project], "GET /api/providers/p2/details": details });
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/agents"]}>
      <App />
    </MemoryRouter>,
  );
}

test("the card says nothing about execution contexts", async () => {
  agents();
  const list = await screen.findByLabelText("Agents");
  expect(within(list).getByRole("heading", { name: "Brief Writer" })).toBeInTheDocument();
  expect(list.textContent).not.toMatch(/context|systemd|administrator/i);
});

test("Advanced details says what has the key and why it doesn't help", async () => {
  agents();
  await userEvent.click(await screen.findByRole("button", { name: "Manage" }));
  await userEvent.click(await screen.findByText("Advanced details"));
  const panel = await screen.findByLabelText("Manage Brief Writer");

  expect(await within(panel).findByText("Execution context")).toBeInTheDocument();
  expect(within(panel).getByText(/Installed system service · brief-writer\.service/)).toBeInTheDocument();
  const facts = within(panel).getByText(/Credentials: Provides OPENAI_API_KEY/);
  expect(facts.textContent).toContain("Can take ad-hoc work: No");
  expect(facts.textContent).toContain("Needs administrator permission: Yes");
  expect(within(panel).getByText(/has no way to be given a request/)).toBeInTheDocument();
});
