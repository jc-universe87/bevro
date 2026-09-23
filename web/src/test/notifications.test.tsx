import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi } from "./helpers";

const notification = (over: Record<string, unknown> = {}) => ({
  id: "n1",
  kind: "automation.matched",
  title: "Competitor watch",
  summary: "Five competitors, including Acme.",
  reason: "A meaningful new competitor was found.",
  read: false,
  task_id: "t1",
  automation_id: "a1",
  delivery: "in_app",
  channels: ["in_app"],
  delivery_problem: null,
  created_at: new Date().toISOString(),
  ...over,
});

const channels = [
  { name: "in_app", label: "In Bevro", available: true, offerable: true, external: false, accepts_destination: false, needs_destination: false, note: null },
  { name: "email", label: "Email", available: false, offerable: false, external: true, accepts_destination: true, needs_destination: false, note: "No mail server is set up on this installation." },
  { name: "webhook", label: "Webhook", available: false, offerable: false, external: true, accepts_destination: true, needs_destination: false, note: null },
];

const automation = (over: Record<string, unknown> = {}) => ({
  id: "a1",
  title: "Competitor watch",
  instruction: "Watch the competitor page",
  mode: "monitoring",
  enabled: true,
  schedule: "Every day · 09:00",
  condition: "Notify when the result changes",
  provider: { id: "p1", slug: "research", name: "Research" },
  next_run_at: new Date(Date.now() + 86_400_000).toISOString(),
  last_run_at: null,
  last_result: null,
  notify: { in_app: true, email: false, webhook: false, on_finish: false, email_to: "", webhook_url: "" },
  created_at: "",
  ...over,
});

function renderAt(path: string) {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

test("something worth saying waits in Bevro, and reading the list does not clear it", async () => {
  const calls = mockApi({
    "GET /api/notifications*": { unread: 1, items: [notification()] },
    "GET /api/providers": [],
    "GET /api/tasks": [],
  });
  renderAt("/notifications");

  expect(await screen.findByText("Competitor watch")).toBeInTheDocument();
  expect(screen.getByText("A meaningful new competitor was found.")).toBeInTheDocument();
  expect(screen.getByText("Unread")).toBeInTheDocument();
  // Nothing was marked read simply because the page was opened.
  expect(calls.some((c) => c.method === "POST" && c.url.includes("/read"))).toBe(false);
});

test("opening the result is reading it, and so is saying so", async () => {
  const calls = mockApi({
    "GET /api/notifications*": { unread: 1, items: [notification()] },
    "POST /api/notifications/n1/read": notification({ read: true }),
    "GET /api/providers": [],
    "GET /api/tasks": [],
  });
  const user = userEvent.setup();
  renderAt("/notifications");

  await user.click(await screen.findByRole("button", { name: "Mark read" }));
  expect(calls.some((c) => c.method === "POST" && c.url === "/api/notifications/n1/read")).toBe(true);
  expect(await screen.findByRole("link", { name: "View result" })).toHaveAttribute("href", "/tasks/t1");
});

test("a message that could not be sent says so, and can be sent again without re-running the work", async () => {
  const failed = notification({ delivery: "delivery_failed", channels: ["email", "in_app"], delivery_problem: "The mail server rejected Bevro's username or password." });
  const calls = mockApi({
    "GET /api/notifications*": { unread: 1, items: [failed] },
    "POST /api/notifications/n1/retry": notification({ delivery: "delivered" }),
    "GET /api/providers": [],
    "GET /api/tasks": [],
  });
  const user = userEvent.setup();
  renderAt("/notifications");

  expect(await screen.findByText(/rejected Bevro/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Try sending again" }));
  expect(calls.some((c) => c.method === "POST" && c.url === "/api/notifications/n1/retry")).toBe(true);
  // Nothing about the task itself was touched.
  expect(calls.some((c) => c.url.startsWith("/api/tasks/") && c.method === "POST")).toBe(false);
});

test("home shows what needs attention when something is waiting", async () => {
  mockApi({
    "GET /api/notifications*": { unread: 1, items: [notification()] },
    "GET /api/providers": [],
    "GET /api/tasks": [],
  });
  renderAt("/");

  const section = await screen.findByRole("region", { name: "Needs your attention" });
  expect(within(section).getByRole("link", { name: "Competitor watch" })).toHaveAttribute("href", "/tasks/t1");
});

test("home stays quiet when there is nothing to say", async () => {
  mockApi({
    "GET /api/notifications*": { unread: 0, items: [] },
    "GET /api/providers": [],
    "GET /api/tasks": [],
  });
  renderAt("/");

  expect(await screen.findByRole("heading", { name: "What should we get done?" })).toBeInTheDocument();
  expect(screen.queryByText("Needs your attention")).not.toBeInTheDocument();
});

test("email is only offered when this installation has it", async () => {
  mockApi({
    "GET /api/automations": [automation()],
    "GET /api/notifications/channels": channels,
    "GET /api/notifications*": { unread: 0, items: [] },
    "GET /api/providers": [],
    "GET /api/tasks": [],
  });
  const user = userEvent.setup();
  renderAt("/scheduled");

  await user.click(await screen.findByRole("button", { name: "Edit" }));
  const section = screen.getByRole("group", { name: "When this needs my attention" });
  expect(within(section).getByRole("checkbox", { name: "In Bevro" })).toBeChecked();
  expect(within(section).queryByRole("checkbox", { name: "Email" })).not.toBeInTheDocument();
});

test("with email set up, an automation can be told to use it", async () => {
  const withEmail = channels.map((c) => (c.name === "email" ? { ...c, available: true, offerable: true, note: null } : c));
  const calls = mockApi({
    "GET /api/automations": [automation()],
    "GET /api/notifications/channels": withEmail,
    "GET /api/notifications*": { unread: 0, items: [] },
    "PATCH /api/automations/a1": automation({ notify: { in_app: true, email: true, webhook: false, on_finish: false, email_to: "", webhook_url: "" } }),
    "GET /api/providers": [],
    "GET /api/tasks": [],
  });
  const user = userEvent.setup();
  renderAt("/scheduled");

  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("checkbox", { name: "Email" }));
  await user.click(screen.getByRole("button", { name: "Save" }));

  const patch = calls.find((c) => c.method === "PATCH");
  expect((patch?.body as { notify: Record<string, boolean> }).notify).toMatchObject({ in_app: true, email: true });
  expect(await screen.findByText(/Also by email/)).toBeInTheDocument();
});

test("an automation can be sent somewhere of its own, and is not asked to before", async () => {
  const withEmail = channels.map((c) =>
    c.name === "email" ? { ...c, available: true, offerable: true, needs_destination: false, note: null } : c,
  );
  const calls = mockApi({
    "GET /api/automations": [automation()],
    "GET /api/notifications/channels": withEmail,
    "GET /api/notifications*": { unread: 0, items: [] },
    "PATCH /api/automations/a1": automation({ notify: { in_app: true, email: true, webhook: false, on_finish: false, email_to: "ops@example.com", webhook_url: "" } }),
    "GET /api/providers": [],
    "GET /api/tasks": [],
  });
  const user = userEvent.setup();
  renderAt("/scheduled");

  await user.click(await screen.findByRole("button", { name: "Edit" }));
  // Nowhere to type an address until the channel is actually chosen.
  expect(screen.queryByLabelText("Email address")).not.toBeInTheDocument();

  await user.click(screen.getByRole("checkbox", { name: "Email" }));
  const address = screen.getByLabelText("Email address");
  expect(address).toHaveAttribute("placeholder", "Somewhere else (optional)");
  await user.type(address, "ops@example.com");
  await user.click(screen.getByRole("button", { name: "Save" }));

  const patch = calls.find((c) => c.method === "PATCH");
  expect((patch?.body as { notify: Record<string, unknown> }).notify).toMatchObject({ email: true, email_to: "ops@example.com" });
});
