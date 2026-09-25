import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi, task } from "./helpers";

const providerRef = { id: "p9", slug: "market-research", name: "Market Research" };
const failedRun = {
  id: "run-1", provider: providerRef, state: "failed", result_summary: null, error_summary: "Market Research needs a credential before it can run.",
  failure: {
    category: "credential_required", title: "Credential required", message: "Market Research needs an OpenAI credential before it can run.",
    actions: [{ kind: "add_credential", label: "Add credential", secret_name: "OPENAI_API_KEY", secret_label: "OpenAI credential" }, { kind: "retry", label: "Retry", secret_name: null, secret_label: null }],
  },
  recovered: false, phase: null, steps: ["Running Market Research"], workspace: null, permissions: [], started_at: "2026-09-21T21:17:04Z", completed_at: "2026-09-21T21:17:08Z",
};
const provider = { id: "p9", slug: "market-research", name: "Market Research", description: "Research", enabled: true, capabilities: [], app_url: null, icon: null, origin: "connected", actions: ["ask"], connection: "command", availability: { state: "available", note: null }, secret_names: [], credentials: [{ name: "OPENAI_API_KEY", label: "OpenAI credential", present: false }], runtime: { display_name: "Runs from this project", availability: "needs_worker", credentials_label: "Missing", runtimes_found: 1, alternatives: 0, health: "available", built: false, review: null, abilities: {} }, created_at: "", updated_at: "" };

function renderAt(path: string) {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

test("a failed task explains itself, offers Add credential and Retry, and hides internals behind Details", async () => {
  const failed = task({ id: "t5", state: "failed", summary: "Market Research stopped with an error.", provider: providerRef, runs: [failedRun] });
  let retried = false;
  const calls = mockApi({
    "GET /api/tasks/t5": () => (retried ? task({ ...failed, state: "queued", summary: null, runs: [failedRun, { ...failedRun, id: "run-2", state: "pending", failure: null }] }) : failed),
    "POST /api/tasks/t5/retry": () => { retried = true; return task({ ...failed, state: "queued", summary: null, runs: [failedRun, { ...failedRun, id: "run-2", state: "pending", failure: null }] }); },
    "GET /api/tasks": [], "GET /api/providers": [provider],
  });
  const user = userEvent.setup();
  renderAt("/tasks/t5");
  const card = await screen.findByRole("region", { name: "What went wrong" });
  expect(within(card).getByRole("heading", { name: "Credential required" })).toBeInTheDocument();
  expect(within(card).getByText("Market Research needs an OpenAI credential before it can run.")).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: "Add credential" })).toHaveAttribute("href", "/agents?manage=p9&credential=1");
  expect(within(card).getByRole("link", { name: "Manage provider" })).toHaveAttribute("href", "/agents?manage=p9");
  expect(screen.queryByText("Market Research stopped with an error.")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/Traceback|argv|exit_code|\/home\//);

  await user.click(within(card).getByText("Details"));
  expect(within(card).getByText("run-1")).toBeInTheDocument();
  expect(within(card).getByText("Credential required", { selector: "dd" })).toBeInTheDocument();

  await user.click(within(card).getByRole("button", { name: "Retry" }));
  expect(calls.some((c) => c.method === "POST" && c.url === "/api/tasks/t5/retry")).toBe(true);
  expect(await screen.findByText("Queued")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "What went wrong" })).not.toBeInTheDocument();
});

test("a provider that has its own credential is not asked for one", async () => {
  mockApi({ "GET /api/providers": [{ ...provider, credentials: [{ name: "OPENAI_API_KEY", label: "OpenAI credential", present: true, source: "host", status: "From this machine" }] }] });
  renderAt("/agents?manage=p9");
  const panel = await screen.findByLabelText("Manage Market Research");
  expect(within(panel).getByText("OpenAI credential · From this machine")).toBeInTheDocument();
  expect(within(panel).queryByLabelText("OpenAI credential")).not.toBeInTheDocument();
  expect(screen.queryByText(/Needs openai credential/)).not.toBeInTheDocument();
});

test("Add credential opens Manage with the field ready; saving never echoes the value", async () => {
  const calls = mockApi({
    "GET /api/providers": [provider],
    "PUT /api/providers/p9/secrets/OPENAI_API_KEY": { ...provider, secret_names: ["OPENAI_API_KEY"], credentials: [{ name: "OPENAI_API_KEY", label: "OpenAI credential", present: true }] },
  });
  const user = userEvent.setup();
  renderAt("/agents?manage=p9&credential=1");
  const panel = await screen.findByLabelText("Manage Market Research");
  expect(within(panel).getByText("OpenAI credential · Missing")).toBeInTheDocument();
  const field = within(panel).getByLabelText("OpenAI credential");
  expect(field).toHaveAttribute("type", "password");
  await user.type(field, "sk-live-secret");
  await user.click(within(panel).getByRole("button", { name: "Save" }));
  expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ value: "sk-live-secret" });
  expect(await within(panel).findByText("OpenAI credential · Added")).toBeInTheDocument();
  expect(within(panel).queryByDisplayValue("sk-live-secret")).not.toBeInTheDocument();
  expect(panel.textContent).not.toContain("sk-live-secret");
});

test("a run that needed a second connection says so quietly, with no mechanism", async () => {
  const done = task({
    id: "t7", state: "completed", summary: "Done. 3 findings.", provider: providerRef,
    runs: [{ ...failedRun, id: "run-9", state: "completed", failure: null, error_summary: null, result_summary: "Done. 3 findings.", recovered: true }],
  });
  mockApi({ "GET /api/tasks/t7": done, "GET /api/tasks": [], "GET /api/providers": [] });
  renderAt("/tasks/t7");
  const outcome = await screen.findByRole("region", { name: "Outcome" });
  expect(within(outcome).getByText("Done. 3 findings.")).toBeInTheDocument();
  expect(within(outcome).getByText("Recovered using another connection.")).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/HTTP|CLI|runtime|fallback|attempt/i);
});

test("Manage says which way in is used, and leaves the fallbacks to Advanced", async () => {
  mockApi({
    "GET /api/providers": [{ ...provider, credentials: [], runtime: { display_name: "Already running on this machine", availability: "ready", credentials_label: "Managed by provider", runtimes_found: 2, alternatives: 1, health: "available", built: false, review: null, abilities: {} } }],
    "POST /api/providers/p9/check": { ok: true, detail: "Already running on this machine. All 2 ways work." },
  });
  const user = userEvent.setup();
  renderAt("/agents?manage=p9");
  const panel = await screen.findByLabelText("Manage Market Research");
  expect(within(panel).getByText("Runs via")).toBeInTheDocument();
  expect(within(panel).getByText("Already running on this machine")).toBeInTheDocument();
  // Fallback machinery is not what someone came to this page for.
  expect(panel.textContent).not.toMatch(/Preferred|Alternatives|1 available/);
  await user.click(within(panel).getByRole("button", { name: "Test all connections" }));
  expect(await within(panel).findByText(/All 2 ways work/)).toBeInTheDocument();
  expect(panel.textContent).not.toMatch(/http|stdio|argv|adapter/i);
});
