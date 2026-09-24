/**
 * Being asked once, in the browser, about one thing.
 *
 * The person types where something is. If it is on this machine, Bevro asks
 * before it looks - showing the path it resolved, so what is agreed to is
 * what will be used - and carries on by itself once they say yes.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi } from "./helpers";

const asking = {
  id: "d1",
  state: "trust_required",
  target_kind: "local",
  target_label: "alpha",
  draft: null,
  trust: {
    kind: "folder",
    label: "alpha",
    path: "/home/someone/projects/alpha",
    parent: "/home/someone/projects",
    parent_label: "projects",
    exists: true,
    is_directory: true,
  },
  error: null,
  test: null,
  provider_id: null,
  created_at: "",
};

function connect(over: Record<string, unknown> = {}) {
  mockApi({ "POST /api/connect/discover": { ...asking, ...over } });
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/connect"]}>
      <App />
    </MemoryRouter>,
  );
}

test("a folder on this machine is asked about in plain words, showing where it really is", async () => {
  connect();
  await userEvent.type(await screen.findByLabelText(/Connect an agent/), "/home/someone/projects/alpha");
  await userEvent.keyboard("{Enter}");

  const ask = await screen.findByLabelText("Permission needed");
  expect(within(ask).getByText("This is a project on this machine.")).toBeInTheDocument();
  expect(within(ask).getByText(/Allow Bevro to look inside this folder/)).toBeInTheDocument();
  // The resolved path, so what is agreed to is what will be used.
  expect(within(ask).getByText("/home/someone/projects/alpha")).toBeInTheDocument();
  expect(within(ask).getByRole("button", { name: "Allow" })).toBeInTheDocument();
  expect(within(ask).getByRole("button", { name: /Allow everything in projects/ })).toBeInTheDocument();

  // Nothing about how Bevro is built.
  expect(ask.textContent).not.toMatch(/BEVRO_LOCAL_ROOTS|Docker|container|worker|root/i);
});

test("saying yes carries straight on, without asking anything else", async () => {
  connect();
  await userEvent.type(await screen.findByLabelText(/Connect an agent/), "/home/someone/projects/alpha");
  await userEvent.keyboard("{Enter}");
  await screen.findByLabelText("Permission needed");

  mockApi({
    "POST /api/connect/discover": asking,
    "POST /api/connect/drafts/d1/trust": { ...asking, state: "looking", trust: null },
    "GET /api/connect/drafts/d1": {
      ...asking,
      state: "found",
      trust: null,
      draft: {
        name: "Alpha",
        description: "Researches things.",
        capabilities: [{ id: "research", title: "Research" }],
        runs_via: "Runs from this project",
        runs_at: null,
        availability: "ready",
        confidence: "high",
        confidence_label: "Confident",
        invocable: true,
        needs_bridge: false,
        choice_needed: false,
        runtime_options: [],
        runtimes_found: 1,
        evidence: [],
        warnings: [],
        auth: { required: false, secret_name: null, label: null, hint: null },
        app_url: null,
        note: null,
        mechanism: "local",
        mechanism_label: "Local",
        invocation_label: null,
        credentials_label: null,
        scope_choices: [],
        connected_for: null,
        source_description: null,
      },
    },
  });
  await userEvent.click(screen.getByRole("button", { name: "Allow" }));

  await waitFor(() => expect(screen.getByLabelText("Found")).toBeInTheDocument(), { timeout: 4000 });
  expect(screen.getByRole("heading", { name: "Alpha" })).toBeInTheDocument();
  expect(screen.queryByLabelText("Permission needed")).not.toBeInTheDocument();
});

test("an address is never asked about: permission is about this machine", async () => {
  connect({
    state: "found",
    target_kind: "url",
    target_label: "http://10.0.0.8:8080",
    trust: null,
    draft: {
      name: "Something",
      description: "Answers questions.",
      capabilities: [],
      runs_via: "Connected over the network",
      runs_at: null,
      availability: "ready",
      confidence: "high",
      confidence_label: "Confident",
      invocable: true,
      needs_bridge: false,
      choice_needed: false,
      runtime_options: [],
      runtimes_found: 1,
      evidence: [],
      warnings: [],
      auth: { required: false, secret_name: null, label: null, hint: null },
      app_url: null,
      note: null,
      mechanism: "http",
      mechanism_label: "API",
      invocation_label: null,
      credentials_label: null,
      scope_choices: [],
      connected_for: null,
      source_description: null,
    },
  });
  await userEvent.type(await screen.findByLabelText(/Connect an agent/), "http://10.0.0.8:8080");
  await userEvent.keyboard("{Enter}");

  expect(await screen.findByLabelText("Found")).toBeInTheDocument();
  expect(screen.queryByLabelText("Permission needed")).not.toBeInTheDocument();
});

test("Settings lists what has been allowed, and offers to take it back", async () => {
  mockApi({
    "GET /api/trust": {
      grants: [
        { id: "g1", kind: "folder", label: "alpha", target: "/home/someone/projects/alpha", scope: "exact", granted_at: "2026-09-24T10:00:00+00:00", granted_by: "person", revoked_at: null },
        { id: "g2", kind: "command", label: "python3", target: "python3", scope: "exact", granted_at: "2026-09-24T10:00:00+00:00", granted_by: "person", revoked_at: null },
      ],
      ceiling: [],
    },
  });
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/settings"]}>
      <App />
    </MemoryRouter>,
  );

  const allowed = await screen.findByLabelText("Allowed");
  expect(within(allowed).getByText("/home/someone/projects/alpha")).toBeInTheDocument();
  expect(within(allowed).getByText("Program: python3")).toBeInTheDocument();
  expect(within(allowed).getAllByRole("button", { name: "Take back" })).toHaveLength(2);
  expect(allowed.textContent).not.toMatch(/BEVRO_LOCAL_ROOTS/);
});
