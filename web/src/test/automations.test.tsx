import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi, task } from "./helpers";

const automation = (over: Record<string, unknown> = {}) => ({
  id: "a1",
  title: "Research competitor changes",
  instruction: "Research competitor changes",
  mode: "scheduled",
  enabled: true,
  schedule: "Every Monday · 09:00",
  condition: null,
  provider: { id: "p1", slug: "research", name: "Research" },
  next_run_at: new Date(Date.now() + 86_400_000 * 3).toISOString(),
  last_run_at: null,
  last_result: null,
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

test("home confirms repeating work instead of creating it silently", async () => {
  const calls = mockApi({
    "POST /api/automations/intent": { recurring: true, title: "research new competitors", instruction: "research new competitors", schedule: "Every Friday · 09:00", condition: null, mode: "scheduled" },
    "POST /api/automations": automation({ schedule: "Every Friday · 09:00" }),
    "GET /api/automations": [],
    "GET /api/tasks": [],
    "GET /api/providers": [],
  });
  const user = userEvent.setup();
  renderAt("/");
  await user.type(screen.getByPlaceholderText("Ask Bevro..."), "Every Friday, research new competitors{Enter}");
  const card = await screen.findByRole("region", { name: "Repeating work" });
  expect(within(card).getByText("Every Friday · 09:00")).toBeInTheDocument();
  // Nothing was submitted or created until the person agreed.
  expect(calls.some((c) => c.url === "/api/tasks")).toBe(false);
  expect(calls.some((c) => c.url === "/api/automations" && c.method === "POST")).toBe(false);

  await user.click(within(card).getByRole("button", { name: "Schedule" }));
  expect(calls.find((c) => c.url === "/api/automations" && c.method === "POST")?.body).toMatchObject({ when: "Every Friday, research new competitors" });
  expect(await screen.findByRole("heading", { level: 1, name: "Scheduled" })).toBeInTheDocument();
});

test("home can still do it just once", async () => {
  const calls = mockApi({
    "POST /api/automations/intent": { recurring: true, title: "check the rota", instruction: "check the rota", schedule: "Every day · 09:00", condition: null, mode: "scheduled" },
    "POST /api/tasks": task({ id: "t3" }),
    "GET /api/tasks/t3": task({ id: "t3", state: "completed", summary: "Done." }),
    "GET /api/tasks": [],
    "GET /api/providers": [],
  });
  const user = userEvent.setup();
  renderAt("/");
  await user.type(screen.getByPlaceholderText("Ask Bevro..."), "Check the rota every day{Enter}");
  await user.click(within(await screen.findByRole("region", { name: "Repeating work" })).getByRole("button", { name: "Just once" }));
  expect(calls.some((c) => c.url === "/api/tasks" && c.method === "POST")).toBe(true);
});

test("scheduled work is a plain list with pause, run now, edit and remove", async () => {
  const calls = mockApi({
    "GET /api/automations": [automation({ last_result: "Ran yesterday" }), automation({ id: "a2", title: "Competitor watch", mode: "monitoring", schedule: "Every day · 09:00", condition: "Notify when the result changes", last_result: "No change last run" })],
    "PATCH /api/automations/a1": automation({ enabled: false, next_run_at: null }),
    "POST /api/automations/a1/run": automation(),
  });
  const user = userEvent.setup();
  renderAt("/scheduled");
  const list = await screen.findByRole("list", { name: "Scheduled work" });
  const rows = within(list).getAllByRole("listitem");
  expect(within(rows[0]).getByText(/Every Monday · 09:00/)).toBeInTheDocument();
  expect(within(rows[0]).getByText(/Ran yesterday/)).toBeInTheDocument();
  expect(within(rows[1]).getByText(/Notify when the result changes/)).toBeInTheDocument();
  expect(within(rows[1]).getByText(/No change last run/)).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/cron|RRULE|FREQ=|UTC offset/i);

  await user.click(within(rows[0]).getByRole("button", { name: "Run now" }));
  expect(await within(rows[0]).findByText("Started. It will appear under Recent.")).toBeInTheDocument();
  await user.click(within(rows[0]).getByRole("button", { name: "Pause" }));
  expect(await within(rows[0]).findByRole("button", { name: "Resume" })).toBeInTheDocument();
  expect(within(rows[0]).getByText("Paused")).toBeInTheDocument();
  expect(calls.find((c) => c.method === "PATCH")?.body).toMatchObject({ enabled: false });
});

test("a finished task can be scheduled in the person's own words", async () => {
  const done = task({ id: "t8", state: "completed", summary: "Done. Three options found.", runs: [], artifacts: [] });
  const calls = mockApi({
    "GET /api/tasks/t8": done,
    "POST /api/automations": automation({ schedule: "Every Monday · 09:00" }),
    "GET /api/tasks": [],
    "GET /api/providers": [],
  });
  const user = userEvent.setup();
  renderAt("/tasks/t8");
  const next = await screen.findByRole("region", { name: "Next" });
  await user.click(within(next).getByRole("button", { name: "Schedule" }));
  await user.type(within(next).getByLabelText("When should Bevro run this?"), "every Monday morning");
  await user.click(within(next).getByLabelText("Only tell me when something changes"));
  await user.click(within(next).getByRole("button", { name: "Schedule" }));
  expect(calls.find((c) => c.url === "/api/automations")?.body).toMatchObject({ when: "every Monday morning", task_id: "t8", only_when: "only tell me when the result changes" });
  expect(await within(next).findByText("Scheduled: Every Monday · 09:00.")).toBeInTheDocument();
});
