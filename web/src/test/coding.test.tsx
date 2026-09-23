import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi, task } from "./helpers";

const run = (over: Record<string, unknown> = {}) => ({
  id: "r1", provider: { id: "p3", slug: "claude-code", name: "Claude Code" }, state: "running", result_summary: null, error_summary: null,
  phase: "Running checks", steps: ["Inspecting the project", "Making changes", "Running checks"], workspace: { id: "w1", name: "Bevro" },
  permissions: ["Read and modify files in Bevro", "Run project commands"], started_at: null, completed_at: null, ...over,
});

test("a coding task asks which project, then shows progress, then the outcome without raw output", async () => {
  let phase = 0;
  const asking = task({ id: "t9", title: "Fix the spacing on Recent", original_request: "Fix the spacing on Recent", state: "needs_input", summary: "Which project should I work on?", provider: { id: "p3", slug: "claude-code", name: "Claude Code" }, runs: [run({ state: "needs_input", steps: [], phase: null, workspace: null, permissions: [] })], input_request: { question: "Which project should I work on?", kind: "choice", options: [{ value: "w1", label: "Bevro" }, { value: "w2", label: "Event Allocation Demo" }] } });
  const working = task({ ...asking, state: "working", summary: null, input_request: null, runs: [run()] });
  const done = task({ ...working, state: "completed", summary: "Done. Recent spacing updated. 2 files changed. Frontend tests pass.", runs: [run({ state: "completed", phase: null })], artifacts: [
    { id: "a1", task_id: "t9", provider_run_id: "r1", type: "report", title: "Summary from Claude Code", summary: null, mime_type: "text/markdown", payload: { text: "## What I did\n\n- Tightened the row padding\n\nSummary: Done." }, external_url: null, content_url: null, metadata: {}, known: true, created_at: "" },
    { id: "a2", task_id: "t9", provider_run_id: "r1", type: "structured", title: "Changed files", summary: null, mime_type: null, payload: { columns: ["File", "Change"], rows: [["web/src/pages/Recent.tsx", "Modified"]] }, external_url: null, content_url: null, metadata: {}, known: true, created_at: "" },
    { id: "a3", task_id: "t9", provider_run_id: "r1", type: "diff", title: "View changes", summary: null, mime_type: "text/x-diff", payload: { text: "--- a/web/src/pages/Recent.tsx\n+++ b/web/src/pages/Recent.tsx\n-py-3\n+py-2.5" }, external_url: null, content_url: null, metadata: {}, known: true, created_at: "" },
  ] });
  const calls = mockApi({
    "GET /api/tasks/t9": () => (phase === 0 ? asking : phase === 1 ? working : done),
    "POST /api/tasks/t9/input": () => { phase = 1; return working; },
    "GET /api/tasks": [], "GET /api/providers": [],
  });
  const user = userEvent.setup();
  render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/tasks/t9"]}><App /></MemoryRouter>);

  expect(await screen.findByText("Which project should I work on?")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Bevro" }));
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({ value: "w1" });
  expect(await screen.findByText("Running checks")).toBeInTheDocument();
  expect(screen.getByText(/Claude Code can: Read and modify files in Bevro/)).toBeInTheDocument();
  phase = 2;
  expect(await screen.findByText("Done. Recent spacing updated. 2 files changed. Frontend tests pass.", {}, { timeout: 3000 })).toBeInTheDocument();
  expect(screen.getByText("Tightened the row padding")).toBeInTheDocument();
  expect(screen.getByRole("table")).toHaveTextContent("web/src/pages/Recent.tsx");
  await user.click(screen.getByText("View changes"));
  expect(screen.getByText(/\+py-2\.5/)).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/tool_use|session_id|stream-json/);
});
