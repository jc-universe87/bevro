import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi, task } from "./helpers";

const providerRef = { id: "p9", slug: "market-research", name: "Market Research" };
const failedRun = {
  id: "run-1", provider: providerRef, state: "failed", result_summary: null, error_summary: "Market Research needs a credential before it can run.",
  failure: {
    category: "credential_required", title: "Needs a credential", message: "Market Research needs an OpenAI credential before it can run.",
    actions: [{ kind: "add_credential", label: "Add credential", secret_name: "OPENAI_API_KEY", secret_label: "OpenAI credential" }, { kind: "retry", label: "Try again", secret_name: null, secret_label: null }],
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

const failedStatus = { kind: "failed", label: "Needs a credential", headline: "Market Research needs an OpenAI credential before it can run.", note: null, quiet: false, since: "2026-09-21T21:17:08Z", can_cancel: false };
const startingStatus = { kind: "starting", label: "Starting", headline: "Starting…", note: null, quiet: false, since: null, can_cancel: true };

test("a failed task explains itself, offers Add credential and Try again, and hides internals behind Details", async () => {
  const failed = task({ id: "t5", state: "failed", summary: "Market Research stopped with an error.", provider: providerRef, runs: [failedRun], status: failedStatus });
  const again = () => task({ ...failed, state: "queued", summary: null, status: startingStatus, runs: [failedRun, { ...failedRun, id: "run-2", state: "pending", failure: null }] });
  let retried = false;
  const calls = mockApi({
    "GET /api/tasks/t5": () => (retried ? again() : failed),
    "POST /api/tasks/t5/retry": () => { retried = true; return again(); },
    "GET /api/tasks": [], "GET /api/providers": [provider], "GET /api/providers/p9": provider,
  });
  const user = userEvent.setup();
  renderAt("/tasks/t5");
  const status = await screen.findByRole("region", { name: "Status" });
  expect(within(status).getByText("Market Research needs an OpenAI credential before it can run.")).toBeInTheDocument();
  expect(within(status).getByRole("link", { name: "Add credential" })).toHaveAttribute("href", "/apps/p9?credential=1");
  // The raw summary is not how a failure is told.
  expect(screen.queryByText("Market Research stopped with an error.")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/Traceback|argv|exit_code|\/home\//);

  // Internals are there for whoever wants them, closed until asked.
  const details = screen.getByText("Details").closest("details")!;
  expect(details).not.toHaveAttribute("open");
  await user.click(screen.getByText("Details"));
  expect(within(details).getByText("Reference run-1")).toBeInTheDocument();
  expect(within(details).getByText("Market Research", { selector: "dd" })).toBeInTheDocument();

  // Nothing reached the app, so trying again asks nothing first.
  await user.click(within(status).getByRole("button", { name: "Try again" }));
  expect(calls.filter((c) => c.method === "POST" && c.url === "/api/tasks/t5/retry")).toHaveLength(1);
  expect(await screen.findByText("Starting…")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Add credential" })).not.toBeInTheDocument();
});

test("a provider that has its own credential is not asked for one", async () => {
  mockApi({ "GET /api/providers/p9": { ...provider, credentials: [{ name: "OPENAI_API_KEY", label: "OpenAI credential", present: true, source: "host", status: "From this machine" }] } });
  renderAt("/apps/p9");
  const care = await screen.findByRole("region", { name: "Settings" });
  expect(within(care).getByText("OpenAI credential · From this machine")).toBeInTheDocument();
  expect(screen.queryByLabelText("OpenAI credential")).not.toBeInTheDocument();
  expect(screen.queryByText(/Needs openai credential/)).not.toBeInTheDocument();
  expect(screen.queryByText("Needs a credential")).not.toBeInTheDocument();
});

test("Add credential opens its page with the field ready; saving never echoes the value", async () => {
  const needsKey = { ...provider, direct: { state: "needs_credential", note: "Bevro needs a credential before it can send it work." } };
  const calls = mockApi({
    "GET /api/providers/p9": needsKey,
    "PUT /api/providers/p9/secrets/OPENAI_API_KEY": { ...provider, direct: { state: "ready", note: "Bevro can send it work." }, secret_names: ["OPENAI_API_KEY"], credentials: [{ name: "OPENAI_API_KEY", label: "OpenAI credential", present: true }] },
  });
  const user = userEvent.setup();
  renderAt("/apps/p9?credential=1");
  const direct = await screen.findByRole("region", { name: "Direct Bevro access" });
  expect(within(direct).getByText("OpenAI credential · Missing")).toBeInTheDocument();
  const field = within(direct).getByLabelText("OpenAI credential");
  expect(field).toHaveFocus();
  expect(field).toHaveAttribute("type", "password");
  await user.type(field, "sk-live-secret");
  await user.click(within(direct).getByRole("button", { name: "Save" }));
  expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ value: "sk-live-secret" });
  const care = screen.getByRole("region", { name: "Settings" });
  expect(await within(care).findByText("OpenAI credential · Added")).toBeInTheDocument();
  expect(await screen.findByText("Bevro can send it work.")).toBeInTheDocument();
  expect(screen.queryByDisplayValue("sk-live-secret")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain("sk-live-secret");
});

test("a run that needed a second connection says so quietly, with no mechanism", async () => {
  const done = task({
    id: "t7", state: "completed", summary: "Done. 3 findings.", provider: providerRef,
    status: { kind: "completed", label: "Completed", headline: "Market Research finished this.", note: "It worked after Bevro tried another way to reach it.", quiet: false, since: "2026-09-21T21:17:08Z", can_cancel: false },
    runs: [{ ...failedRun, id: "run-9", state: "completed", failure: null, error_summary: null, result_summary: "Done. 3 findings.", recovered: true }],
  });
  mockApi({ "GET /api/tasks/t7": done, "GET /api/tasks": [], "GET /api/providers": [] });
  renderAt("/tasks/t7");
  const status = await screen.findByRole("region", { name: "Status" });
  expect(within(status).getByText("Market Research finished this.")).toBeInTheDocument();
  expect(within(status).getByText("It worked after Bevro tried another way to reach it.")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Result" })).getByText("Done. 3 findings.")).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/HTTP|CLI|runtime|fallback|attempt/i);
});

test("its page tests every way in, and says which one is used only under Advanced details", async () => {
  const running = { ...provider, credentials: [], runtime: { display_name: "Already running on this machine", availability: "ready", credentials_label: "Managed by provider", runtimes_found: 2, alternatives: 1, health: "available", built: false, review: null, abilities: {} } };
  mockApi({
    "GET /api/providers/p9": running,
    "GET /api/providers/p9/details": { id: "p9", active_runtime: null, runtimes: [], source_kind: null },
    "POST /api/providers/p9/check": { ok: true, detail: "Already running on this machine. All 2 ways work.", checks: [] },
  });
  const user = userEvent.setup();
  renderAt("/apps/p9");
  const care = await screen.findByRole("region", { name: "Settings" });
  // Fallback machinery is not what someone came to this page for.
  expect(document.body.textContent).not.toMatch(/Preferred|Alternatives|1 available|Runs via/);
  await user.click(within(care).getByRole("button", { name: "Test" }));
  expect(await within(care).findByText(/All 2 ways work/)).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/http|stdio|argv|adapter/i);

  await user.click(within(care).getByText("Advanced details"));
  expect(await within(care).findByText("Runs via")).toBeInTheDocument();
  expect(within(care).getByText("Already running on this machine")).toBeInTheDocument();
});
