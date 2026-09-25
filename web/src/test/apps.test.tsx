/**
 * Apps & agents, and one app or agent's own page. Synthetic shapes only:
 * something Bevro drives, a web app it can't, something that runs on a
 * schedule and posts its results, something that stopped answering.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi } from "./helpers";

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

const ledger = {
  ...base,
  id: "p1",
  slug: "ledger",
  name: "Ledger",
  description: "Keeps invoices and answers questions about them.",
  capabilities: [{ id: "invoices", title: "Invoices" }, { id: "reports", title: "Reports" }],
  actions: ["ask", "open"],
  connection: "api",
  direct: { state: "ready", note: "Bevro can send it work." },
  surfaces: [{ kind: "web_app", role: "use", label: "Web app", sentence: "It has its own web app.", url: "https://ledger.example.net/app", reach: "network" }],
  runtime: { display_name: "Connected over the network", availability: "ready", credentials_label: "None needed", runtimes_found: 1, alternatives: 0, health: "available", built: false, review: null, abilities: {} },
};

const notebook = {
  ...base,
  id: "p2",
  slug: "notebook",
  name: "Notebook",
  description: "Keeps what you have noted and learned.",
  capabilities: [{ id: "knowledge", title: "Search what you know" }],
  actions: ["open"],
  direct: { state: "not_set_up", note: "Bevro can't send it work directly yet." },
  surfaces: [{ kind: "web_app", role: "use", label: "Web app", sentence: "It has its own web app.", url: "https://notebook.example.net:8443", reach: "shared" }],
  availability: { state: "unavailable", note: "No way in that Bevro can use", reason: "nothing_usable" },
};

const briefing = {
  ...base,
  id: "p3",
  slug: "briefing",
  name: "Briefing",
  description: "Researches the market and writes a weekly briefing.",
  capabilities: [{ id: "research", title: "Research" }],
  connection: "command",
  direct: { state: "needs_credential", note: "Bevro needs a credential before it can send it work." },
  availability: { state: "unavailable", note: "Needs a credential", reason: "needs_credential" },
  credentials: [{ name: "OPENAI_API_KEY", label: "OpenAI credential", present: false, source: "missing", status: "Missing", note: "Briefing already has a credential for its scheduled runs, but that credential isn't available when Bevro starts a new task.", why: "The credential is handed over only when its scheduled service starts." }],
  surfaces: [
    { kind: "telegram", role: "delivers", label: "Telegram", sentence: "It sends its results to Telegram." },
    { kind: "schedule", role: "runs", label: "Scheduled runs", sentence: "It runs by itself, every Monday at 07:30 UTC.", when: "every Monday at 07:30 UTC", installed: true },
    { kind: "command_line", role: "use", label: "Command line", sentence: "It can be run from the command line on this machine." },
  ],
};

const stopped = {
  ...base,
  id: "p4",
  slug: "sales",
  name: "Sales Desk",
  description: "Prepares quotes.",
  actions: ["ask"],
  direct: { state: "unreachable", note: "Bevro could send it work before, but can't reach it right now." },
};

function renderAt(path: string) {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

const item = (name: string) => screen.getByRole("heading", { name }).closest("li")!;

test("each item says what it is for, how it is used, and offers the one useful thing", async () => {
  mockApi({ "GET /api/providers": [ledger, notebook, briefing, stopped] });
  renderAt("/apps");
  expect(await screen.findByRole("heading", { level: 1, name: "Apps & agents" })).toBeInTheDocument();
  const list = screen.getByRole("list", { name: "Apps and agents" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(4);

  const l = item("Ledger");
  expect(l).toHaveTextContent("Keeps invoices and answers questions about them.");
  expect(l).toHaveTextContent("Available through Bevro · Web app");
  expect(within(l).getAllByRole("button")[0]).toHaveTextContent("Use in Bevro");
  expect(within(l).getByRole("link", { name: /Open app/ })).toHaveAttribute("href", "https://ledger.example.net/app");

  // A web app Bevro can't drive: healthy, and opening it is the thing to do.
  const n = item("Notebook");
  expect(n).toHaveTextContent("Available through Web app");
  const open = within(n).getByRole("link", { name: /Open Notebook/ });
  expect(open).toHaveAttribute("href", "https://notebook.example.net:8443/");
  expect(open).toHaveAttribute("target", "_blank");
  expect(within(n).getByRole("button", { name: "Set up direct access" })).toBeInTheDocument();
  expect(n.textContent).not.toMatch(/can't be reached|not usable|no connection|needs setup|no way in|error/i);
  expect(n.querySelector(".bv-dot-attention")).toBeNull();

  // Runs on a schedule and posts its results: said, and not dressed up as a way in.
  const b = item("Briefing");
  expect(b).toHaveTextContent("Available through Scheduled runs · Results to Telegram");
  expect(within(b).getAllByRole("button").map((x) => x.textContent)).toEqual(["Add credential", "How to use it"]);
  expect(b).toHaveTextContent("Direct use in Bevro needs a credential");
  expect(b.querySelector(".bv-dot-attention")).toBeNull(); // the app is fine; nothing to warn about

  // A way in that stopped working is the one real problem here.
  const s = item("Sales Desk");
  expect(s).toHaveTextContent("Can't be reached right now");
  expect(s.querySelector(".bv-dot-attention")).not.toBeNull();
  expect(within(s).getAllByRole("button")[0]).toHaveTextContent("Try again");

  // No machinery in the ordinary view.
  expect(list.textContent).not.toMatch(/provider|runtime|adapter|endpoint|openapi|http|mcp|docker|systemd|proxy|schema/i);
});

test("Use in Bevro asks it directly", async () => {
  const calls = mockApi({ "GET /api/providers": [ledger], "POST /api/tasks": { id: "t1" }, "GET /api/tasks/t1": { id: "t1", title: "x", original_request: "x", state: "queued", summary: null, provider: null, created_at: "", updated_at: "", completed_at: null, runs: [], artifacts: [], input_request: null } });
  const user = userEvent.setup();
  renderAt("/apps");
  await user.click(await screen.findByRole("button", { name: "Use in Bevro" }));
  const field = screen.getByLabelText("What should Ledger do?");
  expect(field).toHaveFocus();
  await user.type(field, "Total for March{Enter}");
  expect(calls.find((c) => c.method === "POST")?.body).toEqual({ request: "Total for March", provider_id: "p1" });
});

test("Try again checks it and shows what is true now", async () => {
  const calls = mockApi({ "GET /api/providers": [stopped], "POST /api/providers/p4/check": { ok: true, detail: null, checks: [] }, "GET /api/providers/p4": { ...stopped, direct: { state: "ready" } } });
  const user = userEvent.setup();
  renderAt("/apps");
  await user.click(await screen.findByRole("button", { name: "Try again" }));
  expect(calls.some((c) => c.method === "POST" && c.url === "/api/providers/p4/check")).toBe(true);
  expect(await screen.findByRole("button", { name: "Use in Bevro" })).toBeInTheDocument();
});

test("an empty hub says what will be here and the two ways to fill it", async () => {
  mockApi({ "GET /api/providers": [] });
  renderAt("/apps");
  const empty = await screen.findByRole("region", { name: "Your apps and agents will appear here." });
  expect(within(empty).getByText("Connect something you already use, or create something new.")).toBeInTheDocument();
  expect(within(empty).getByRole("link", { name: "Connect" })).toHaveAttribute("href", "/connect");
  expect(within(empty).getByRole("link", { name: "Create" })).toHaveAttribute("href", "/create");
  expect(document.body.textContent).not.toMatch(/No providers/i);
});

test("when the list can't be loaded it says so, and how to try again", async () => {
  mockApi({});
  renderAt("/apps");
  expect(await screen.findByRole("alert")).toHaveTextContent("Bevro couldn't load your apps and agents.");
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
});

// --------------------------------------------------------------------------- one app or agent

test("its page reads in the order a person asks: what, what it can do, how to use it, then direct access", async () => {
  mockApi({ "GET /api/providers/p2": notebook, "GET /api/providers": [notebook] });
  renderAt("/apps/p2");
  expect(await screen.findByRole("heading", { level: 1, name: "Notebook" })).toBeInTheDocument();
  const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
  expect(headings).toEqual(["What it can do", "How you can use it", "Direct Bevro access", "Settings"]);
  expect(screen.getAllByRole("link", { name: /Open Notebook/ })[0]).toHaveAttribute("href", "https://notebook.example.net:8443/");
  const direct = screen.getByRole("region", { name: "Direct Bevro access" });
  expect(direct).toHaveTextContent("Notebook doesn't accept tasks from Bevro yet. You can still use it the ways above.");
  expect(within(direct).getByRole("button", { name: "Look again" })).toBeInTheDocument();
  expect(within(direct).getByRole("button", { name: "Why can't Bevro use it directly?" })).toBeInTheDocument();
  // Nothing on the page competes with opening it: maintenance for a thing
  // Bevro doesn't drive is only Advanced details and removal.
  const care = screen.getByRole("region", { name: "Settings" });
  expect(within(care).queryByRole("button", { name: "Test" })).not.toBeInTheDocument();
  expect(within(care).queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
});

test("a web app only this computer can open says where, instead of a broken link, and takes the address that works", async () => {
  const localOnly = { ...notebook, surfaces: [{ kind: "web_app", role: "use", label: "Web app", sentence: "It has its own web app.", url: "http://127.0.0.1:6400/", reach: "loopback" }] };
  const calls = mockApi({ "GET /api/providers/p2": localOnly, "PATCH /api/providers/p2": { ...localOnly, surfaces: [{ ...localOnly.surfaces[0], url: "https://notes.example.org/", reach: "explicit" }] } });
  // This browser is on another device.
  const where = window.location;
  Object.defineProperty(window, "location", { configurable: true, value: { ...where, hostname: "100.64.0.7", port: "6140" } });
  try {
    const user = userEvent.setup();
    renderAt("/apps/p2");
    const use = await screen.findByRole("region", { name: "How you can use it" });
    expect(within(use).queryByRole("link", { name: /Open Notebook/ })).not.toBeInTheDocument();
    expect(use).toHaveTextContent("Notebook only opens on the computer it runs on, at http://127.0.0.1:6400");
    await user.click(within(use).getByRole("button", { name: "Give Bevro the address you use" }));
    await user.type(within(use).getByLabelText("Address of Notebook"), "https://notes.example.org/{Enter}");
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ web_address: "https://notes.example.org/" });
    expect(await screen.findAllByRole("link", { name: /Open Notebook/ })).not.toHaveLength(0);
  } finally {
    Object.defineProperty(window, "location", { configurable: true, value: where });
  }
});

test("something Bevro drives: Test, pause, and remove are there, below the useful part", async () => {
  const calls = mockApi({
    "GET /api/providers/p1": ledger,
    "POST /api/providers/p1/check": { ok: true, detail: "Everything Bevro can check is in place.", checks: [{ label: "Bevro can reach it", ok: true }, { label: "No real task was run", ok: null, kind: "functional" }] },
    "GET /api/providers/p1/removal": { removable: true, history: 2, in_flight: 0, credentials: 0, built_project: false, built_connection: false },
    "DELETE /api/providers/p1": {},
    "GET /api/providers": [],
  });
  const user = userEvent.setup();
  renderAt("/apps/p1");
  const care = await screen.findByRole("region", { name: "Settings" });
  expect(document.body.textContent).not.toMatch(/argv|cwd|python -m|stdio|systemd/);
  await user.click(within(care).getByRole("button", { name: "Test" }));
  const results = await within(care).findByRole("region", { name: "Test results" });
  expect(within(results).getByText("Test passed.")).toBeInTheDocument();
  expect(within(care).getByRole("button", { name: "Pause" })).toBeInTheDocument();

  await user.click(within(care).getByRole("button", { name: "Remove from Bevro" }));
  const question = within(care).getByRole("group", { name: "Remove Ledger from Bevro?" });
  expect(question).toHaveTextContent("Nothing outside Bevro is touched: Ledger itself stays exactly as it is.");
  expect(question).toHaveTextContent("2 tasks stay in Recent");
  await user.click(within(question).getByRole("button", { name: "Yes, remove" }));
  expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/providers/p1")).toBe(true);
  expect(await screen.findByRole("heading", { level: 1, name: "Apps & agents" })).toBeInTheDocument();
});

test("a missing credential is explained where direct access is, and asked for once", async () => {
  mockApi({ "GET /api/providers/p3": briefing });
  const user = userEvent.setup();
  renderAt("/apps/p3?credential=1");
  const direct = await screen.findByRole("region", { name: "Direct Bevro access" });
  const field = within(direct).getByLabelText("OpenAI credential");
  expect(field).toHaveFocus();
  expect(field).toHaveAttribute("type", "password");
  expect(direct.textContent?.match(/already has a credential for its scheduled runs/g)).toBeNull(); // the note waits until the form is closed
  await user.click(within(direct).getByRole("button", { name: "Cancel" }));
  expect(direct.textContent?.match(/already has a credential for its scheduled runs/g)).toHaveLength(1);
  expect(within(direct).getByRole("button", { name: "Why can't Bevro use the existing one?" })).toBeInTheDocument();
  expect(direct.textContent).not.toMatch(/systemd|EnvironmentFile/);
  const use = screen.getByRole("region", { name: "How you can use it" });
  expect(use).toHaveTextContent("It sends its results to Telegram.");
  expect(use).toHaveTextContent("It runs by itself, every Monday at 07:30 UTC.");
});

test("links from before Apps & agents had pages still arrive at the right place", async () => {
  mockApi({ "GET /api/providers/p3": briefing });
  renderAt("/agents?manage=p3&credential=1");
  expect(await screen.findByRole("heading", { level: 1, name: "Briefing" })).toBeInTheDocument();
  expect(screen.getByLabelText("OpenAI credential")).toHaveFocus();
});

test("something removed says so plainly", async () => {
  mockApi({}); // the server answers 404
  renderAt("/apps/gone");
  expect(await screen.findByRole("heading", { level: 1, name: "Not found" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Back to Apps & agents" })).toHaveAttribute("href", "/apps");
});

// --------------------------------------------------------------------------- 12, 13: navigation and keyboard

test("navigation names Apps & agents, and a phone gets a short label with the full name for screen readers", async () => {
  mockApi({ "GET /api/providers": [] });
  renderAt("/apps");
  const navs = screen.getAllByRole("navigation", { name: "Main" });
  expect(navs).toHaveLength(2); // the sidebar, and the phone's bar
  for (const nav of navs) {
    expect(within(nav).getByRole("link", { name: "Apps & agents" })).toHaveAttribute("href", "/apps");
    expect(within(nav).queryByRole("link", { name: "Agents" })).not.toBeInTheDocument();
  }
  const phone = navs[1];
  expect(within(phone).getByRole("link", { name: "Apps & agents" })).toHaveTextContent("Apps");
});

test("the phone's More menu opens and closes from the keyboard", async () => {
  mockApi({ "GET /api/providers": [] });
  const user = userEvent.setup();
  renderAt("/");
  const phone = screen.getAllByRole("navigation", { name: "Main" })[1];
  const more = within(phone).getByRole("button", { name: "More" });
  more.focus();
  await user.keyboard("{Enter}");
  expect(more).toHaveAttribute("aria-expanded", "true");
  expect(within(phone).getByRole("link", { name: "Connect" })).toBeInTheDocument();
  await user.keyboard("{Escape}");
  expect(more).toHaveAttribute("aria-expanded", "false");
});

test("every action on an item is reachable with Tab, in reading order", async () => {
  mockApi({ "GET /api/providers": [notebook] });
  const user = userEvent.setup();
  renderAt("/apps");
  const n = await screen.findByRole("heading", { name: "Notebook" });
  const li = n.closest("li")!;
  const name = within(li).getByRole("link", { name: "Notebook" });
  name.focus();
  await user.tab();
  expect(within(li).getByRole("link", { name: /Open Notebook/ })).toHaveFocus();
  await user.tab();
  expect(within(li).getByRole("button", { name: "Set up direct access" })).toHaveFocus();
});

test("an agent Bevro created says which version is in use, and that a new one is being built", async () => {
  const made = { ...ledger, id: "p6", name: "Digest", origin: "created", surfaces: [], actions: ["ask"], build: { purpose: "Summarise the week's notes.", version: 2, state: "building", built_by: null, needs: [], can_rebuild: true } };
  mockApi({ "GET /api/providers/p6": made });
  renderAt("/apps/p6");
  const can = await screen.findByRole("region", { name: "What it can do" });
  expect(can).toHaveTextContent("Summarise the week's notes.");
  expect(can).toHaveTextContent("Version 2 · building a new version");
  expect(screen.getByText("Building a new version")).toBeInTheDocument();
  expect(document.querySelector(".bv-dot-attention")).toBeNull(); // progress, not a problem
});

test("on its page, Add credential appears once and opens the field where direct access is", async () => {
  mockApi({ "GET /api/providers/p3": briefing });
  const user = userEvent.setup();
  renderAt("/apps/p3");
  await screen.findByRole("heading", { level: 1, name: "Briefing" });
  const buttons = screen.getAllByRole("button", { name: "Add credential" });
  expect(buttons).toHaveLength(1);
  await user.click(buttons[0]);
  const direct = screen.getByRole("region", { name: "Direct Bevro access" });
  expect(within(direct).getByLabelText("OpenAI credential")).toHaveFocus();
});
