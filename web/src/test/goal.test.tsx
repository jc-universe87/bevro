/**
 * Home as the front door: say what you want done, and Bevro says which of
 * your apps and agents is for it, why, and what to do next - starting the
 * work itself only when it is sure and can. Synthetic items only.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import type { RouteAnswer } from "../lib/api";
import { mockApi, task } from "./helpers";

const base = {
  enabled: true,
  capabilities: [],
  app_url: null,
  icon: null,
  origin: "connected",
  actions: [],
  connection: null,
  availability: { state: "available", note: null },
  secret_names: [],
  credentials: [],
  runtime: null,
  surfaces: [],
  details: {},
  created_at: "",
  updated_at: "",
};

const desk = { ...base, id: "p1", slug: "desk", name: "Jobs Desk", description: "", actions: ["ask"], direct: { state: "ready", note: "" } };
const notebook = {
  ...base,
  id: "p2",
  slug: "notebook",
  name: "Notebook",
  description: "Keeps what you have noted and learned.",
  actions: ["open"],
  direct: { state: "not_set_up", note: "" },
  surfaces: [{ kind: "web_app", role: "use", label: "Web app", sentence: "", url: "https://notebook.example.net", reach: "shared" }],
};
const briefing = { ...base, id: "p3", slug: "briefing", name: "Briefing", description: "Researches the widget market.", direct: { state: "needs_credential", note: "" } };
const binder = {
  ...base,
  id: "p4",
  slug: "binder",
  name: "Binder",
  description: "",
  actions: ["open"],
  direct: { state: "unreachable", note: "" },
  surfaces: [{ kind: "web_app", role: "use", label: "Web app", sentence: "", url: "https://binder.example.net", reach: "shared" }],
};

// The task page, arrived at: its title is the task's.
const TITLE = task().title;

const answer = (over: Partial<RouteAnswer>): RouteAnswer => ({ outcome: "none", message: "", sure: false, item: null, why: null, choices: [], ...over });

function home(route: (body: { request: string; provider_id?: string }) => RouteAnswer) {
  const calls = mockApi({
    "GET /api/providers": [desk, notebook, briefing, binder],
    "GET /api/notifications*": { items: [], unread: 0 },
    "POST /api/automations/intent": { recurring: false },
    "POST /api/route": (_url: string, init?: RequestInit) => route(JSON.parse(String(init?.body))),
    "POST /api/tasks": task({ id: "t9" }),
    "GET /api/tasks/t9": task({ id: "t9" }),
    "POST /api/providers/p4/check": { ok: true, checks: [] },
    "GET /api/providers/p3": briefing,
  });
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );
  return calls;
}

const say = async (user: ReturnType<typeof userEvent.setup>, text: string) => user.type(await screen.findByRole("textbox", { name: "What do you want to get done?" }), `${text}{Enter}`);
const posted = (calls: ReturnType<typeof mockApi>) => calls.filter((c) => c.method === "POST" && c.url === "/api/tasks").map((c) => c.body);

test("a sure answer that Bevro can act on simply starts the work", async () => {
  const calls = home(() => answer({ outcome: "direct", sure: true, message: "Jobs Desk can handle this directly.", item: { id: "p1", name: "Jobs Desk" } }));
  const user = userEvent.setup();
  await say(user, "Help me review my current job opportunities.");
  expect(await screen.findByRole("heading", { level: 1, name: TITLE })).toBeInTheDocument();
  // The router chose, so the task service routes it and records that.
  expect(posted(calls)).toEqual([{ request: "Help me review my current job opportunities." }]);
});

test("an app Bevro can't drive is the answer: open it, and nothing is started", async () => {
  const calls = home(() => answer({ outcome: "handoff", sure: true, message: "Notebook is the best place for this.", item: { id: "p2", name: "Notebook" }, why: "Keeps what you have noted and learned." }));
  const user = userEvent.setup();
  await say(user, "Where did I write about pricing?");
  const region = await screen.findByRole("region", { name: "Notebook is the best place for this." });
  const open = within(region).getByRole("link", { name: /Open Notebook/ });
  expect(open).toHaveAttribute("href", "https://notebook.example.net/");
  expect(open).toHaveAttribute("target", "_blank");
  expect(posted(calls)).toEqual([]);
  // Not an error, and not dressed as one.
  expect(screen.queryByRole("alert")).toBeNull();

  // Why, in a sentence, only when asked.
  const why = within(region).getByRole("button", { name: /Why Notebook\?/ });
  expect(within(region).queryByText("Keeps what you have noted and learned.")).not.toBeVisible();
  await user.click(why);
  expect(within(region).getByText("Keeps what you have noted and learned.")).toBeVisible();
  expect(region.textContent).not.toMatch(/\d\.\d|score|confidence|routing|provider|runtime/i);
});

test("a missing credential is said up front, with the way to add it and how it's used meanwhile", async () => {
  home(() => answer({ outcome: "blocked", sure: true, message: "Briefing can do this, but direct use in Bevro needs a credential.", item: { id: "p3", name: "Briefing" } }));
  const user = userEvent.setup();
  await say(user, "Research the widget market");
  const region = await screen.findByRole("region", { name: /needs a credential/ });
  expect(within(region).getAllByRole("button").map((b) => b.textContent)).toEqual(["Add credential", "How to use it"]);
  await user.click(within(region).getByRole("button", { name: "Add credential" }));
  expect(await screen.findByRole("heading", { level: 1, name: "Briefing" })).toBeInTheDocument();
});

test("the right one being unreachable offers Try again and its app, and tries again for that one", async () => {
  let asked = 0;
  const calls = home((body) => {
    asked += 1;
    return body.provider_id
      ? answer({ outcome: "handoff", sure: true, message: "Binder is the best place for this.", item: { id: "p4", name: "Binder" } })
      : answer({ outcome: "unavailable", sure: true, message: "Binder is the right one for this, but I can't reach it right now.", item: { id: "p4", name: "Binder" } });
  });
  const user = userEvent.setup();
  await say(user, "Where is my passport scan filed?");
  const region = await screen.findByRole("region", { name: /can't reach it right now/ });
  expect(within(region).getByRole("link", { name: /Open app/ })).toHaveAttribute("href", "https://binder.example.net/");
  await user.click(within(region).getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("region", { name: "Binder is the best place for this." })).toBeInTheDocument();
  expect(calls.some((c) => c.url === "/api/providers/p4/check")).toBe(true);
  expect(calls.filter((c) => c.url === "/api/route").map((c) => c.body)).toEqual([
    { request: "Where is my passport scan filed?" },
    { request: "Where is my passport scan filed?", provider_id: "p4" },
  ]);
  expect(asked).toBe(2);
});

test("a likely but not certain answer waits for the person before starting", async () => {
  const calls = home(() => answer({ outcome: "direct", sure: false, message: "Jobs Desk looks like the right one for this.", item: { id: "p1", name: "Jobs Desk" }, why: "Jobs Desk works with opportunities." }));
  const user = userEvent.setup();
  await say(user, "Sort out my shortlist");
  const region = await screen.findByRole("region", { name: "Jobs Desk looks like the right one for this." });
  expect(posted(calls)).toEqual([]);
  await user.click(within(region).getByRole("button", { name: "Use Jobs Desk" }));
  expect(await screen.findByRole("heading", { level: 1, name: TITLE })).toBeInTheDocument();
  // The person chose, so the task says so.
  expect(posted(calls)).toEqual([{ request: "Sort out my shortlist", provider_id: "p1" }]);
});

test("two close matches are a choice, and the one chosen is used for this request", async () => {
  const calls = home((body) =>
    body.provider_id === "p1"
      ? answer({ outcome: "direct", sure: true, message: "Jobs Desk can handle this directly.", item: { id: "p1", name: "Jobs Desk" } })
      : answer({
          outcome: "choice",
          message: "I found two apps that could help.",
          choices: [
            { id: "p1", name: "Jobs Desk", summary: "Review opportunities and applications" },
            { id: "p4", name: "Binder", summary: "Filing and finding documents" },
          ],
        }),
  );
  const user = userEvent.setup();
  await say(user, "I need to work on some documents");
  const list = await screen.findByRole("list", { name: "Apps and agents that could help" });
  expect(within(list).getAllByRole("button").map((b) => b.textContent)).toEqual(["Jobs DeskReview opportunities and applications", "BinderFiling and finding documents"]);
  expect(posted(calls)).toEqual([]);
  await user.click(within(list).getByRole("button", { name: /Jobs Desk/ }));
  expect(await screen.findByRole("heading", { level: 1, name: TITLE })).toBeInTheDocument();
  expect(posted(calls)).toEqual([{ request: "I need to work on some documents", provider_id: "p1" }]);
});

test("choosing an app Bevro can't drive from a choice shows where to open it", async () => {
  home((body) =>
    body.provider_id === "p2"
      ? answer({ outcome: "handoff", sure: true, message: "Notebook is the best place for this.", item: { id: "p2", name: "Notebook" } })
      : answer({ outcome: "choice", message: "I found two apps that could help.", choices: [{ id: "p2", name: "Notebook", summary: "" }, { id: "p4", name: "Binder", summary: "" }] }),
  );
  const user = userEvent.setup();
  await say(user, "Find my notes about the garden");
  await user.click(await screen.findByRole("button", { name: "Notebook" }));
  expect(await screen.findByRole("link", { name: /Open Notebook/ })).toBeInTheDocument();
});

test("nothing that fits is said plainly, with the two ways to add something", async () => {
  const calls = home(() => answer({ outcome: "none", message: "I don't have an app or agent that looks suited to this yet." }));
  const user = userEvent.setup();
  await say(user, "Book me a dentist appointment.");
  const region = await screen.findByRole("region", { name: "I don't have an app or agent that looks suited to this yet." });
  expect(within(region).getByRole("link", { name: "Connect something" })).toHaveAttribute("href", "/connect");
  expect(within(region).getByRole("link", { name: "Create something new" })).toHaveAttribute("href", "/create");
  expect(posted(calls)).toEqual([]);
});

test("typing something new clears the last answer", async () => {
  home(() => answer({ outcome: "none", message: "I don't have an app or agent that looks suited to this yet." }));
  const user = userEvent.setup();
  await say(user, "Book me a dentist appointment.");
  await screen.findByRole("region", { name: /suited to this yet/ });
  await user.type(screen.getByRole("textbox", { name: "What do you want to get done?" }), " please");
  expect(screen.queryByRole("region", { name: /suited to this yet/ })).toBeNull();
});
