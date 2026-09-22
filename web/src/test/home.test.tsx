import { render, screen, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi, task } from "./helpers";

test("home shows one obvious place to type and no agent selector", async () => {
  mockApi({ "GET /api/tasks": [], "GET /api/providers": [] });
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );
  expect(screen.getByRole("heading", { level: 1, name: "What should we get done?" })).toBeInTheDocument();
  const input = screen.getByPlaceholderText("Ask Bevro...");
  expect(input).toHaveFocus();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
});

test("asking creates a task, shows it working, then shows the outcome and deep link", async () => {
  let polls = 0;
  const done = task({
    state: "completed",
    summary: "Done. 148 participants allocated. 7 need review.",
    artifacts: [
      { id: "a1", task_id: "t1", provider_run_id: null, type: "deep_link", title: "Review in Moimio", summary: null, mime_type: null, payload: null, external_url: "https://moimio.example/app/events/x", content_url: null, metadata: {}, known: true, created_at: "" },
    ],
  });
  const calls = mockApi({
    "POST /api/automations/intent": { recurring: false },
    "POST /api/tasks": task(),
    "GET /api/tasks/t1": () => (++polls < 2 ? task({ state: "working" }) : done),
    "GET /api/tasks": [],
    "GET /api/providers": [],
  });
  const user = userEvent.setup();
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );
  await user.type(screen.getByPlaceholderText("Ask Bevro..."), "Allocate participants for the spring conference{Enter}");

  // Home checks whether this repeats, then sends the one-off task as always.
  expect(calls.find((c) => c.url === "/api/automations/intent")?.body).toMatchObject({ text: "Allocate participants for the spring conference" });
  expect(calls.find((c) => c.url === "/api/tasks" && c.method === "POST")?.body).toEqual({ request: "Allocate participants for the spring conference" });
  expect(await screen.findByText("Working…")).toBeInTheDocument();
  expect(await screen.findByText("Done. 148 participants allocated. 7 need review.", {}, { timeout: 3000 })).toBeInTheDocument();
  const link = screen.getByRole("link", { name: /Review in Moimio/ });
  expect(link).toHaveAttribute("href", "https://moimio.example/app/events/x");
  await waitFor(() => expect(screen.getByText("Done")).toBeInTheDocument());
});

test("when nothing can take the request, Home says so and offers Connect and Create", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "POST") return new Response(JSON.stringify({ detail: { message: "I don't have a connected provider for that yet.", reason: "no_provider" } }), { status: 503 });
      return new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  const user = userEvent.setup();
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );
  await user.type(screen.getByPlaceholderText("Ask Bevro..."), "Write a poem about autumn{Enter}");
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("I don't have a connected provider for that yet.");
  const { getByRole } = within(alert);
  expect(getByRole("link", { name: "Connect" })).toHaveAttribute("href", "/connect");
  expect(getByRole("link", { name: "Create" })).toHaveAttribute("href", "/create");
});
