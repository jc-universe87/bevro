/**
 * Every item in Apps & agents can be opened, looked into, and taken out of
 * Bevro from the list itself - on a phone, with a mouse, or with the keyboard
 * alone. Synthetic shapes only: something Bevro drives, a web app it can't,
 * and something that needs a credential before Bevro can use it directly.
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

// Bevro sends it work directly.
const ledger = {
  ...base,
  id: "p1",
  slug: "ledger",
  name: "Ledger",
  description: "Keeps invoices and answers questions about them.",
  actions: ["ask"],
  connection: "api",
  direct: { state: "ready", note: "Bevro can send it work." },
};

// A web app Bevro can only point at.
const notebook = {
  ...base,
  id: "p2",
  slug: "notebook",
  name: "Notebook",
  description: "Keeps what you have noted and learned.",
  actions: ["open"],
  direct: { state: "not_set_up", note: "Bevro can't send it work directly yet." },
  surfaces: [{ kind: "web_app", role: "use", label: "Web app", sentence: "It has its own web app.", url: "https://notebook.example.net", reach: "shared" }],
  availability: { state: "unavailable", note: "No way in that Bevro can use", reason: "nothing_usable" },
};

// Runs by itself; direct use waits on a credential.
const briefing = {
  ...base,
  id: "p3",
  slug: "briefing",
  name: "Briefing",
  description: "Researches the market and writes a weekly briefing.",
  connection: "command",
  direct: { state: "needs_credential", note: "Bevro needs a credential before it can send it work." },
  availability: { state: "unavailable", note: "Needs a credential", reason: "needs_credential" },
  credentials: [{ name: "SERVICE_KEY", label: "Service credential", present: false, source: "missing", status: "Missing" }],
  surfaces: [{ kind: "schedule", role: "runs", label: "Scheduled runs", sentence: "It runs by itself.", when: "every Monday", installed: true }],
};

const plan = { removable: true, history: 3, in_flight: 0, credentials: 0, built_project: false, built_connection: false };

/** A server that really lets go: what is deleted is not listed again. */
function server(initial: Array<{ id: string }>) {
  let items = [...initial];
  const calls = mockApi({
    "GET /api/providers": () => items,
    "GET /api/providers/*": (url: string) => (url.endsWith("/removal") ? plan : items.find((p) => url === `/api/providers/${p.id}`) ?? {}),
    "DELETE /api/providers/*": (url: string) => {
      items = items.filter((p) => url !== `/api/providers/${p.id}`);
      return {};
    },
  });
  return calls;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

const row = async (name: string) => (await screen.findByRole("heading", { level: 2, name })).closest("li")!;
const more = (li: HTMLElement, name: string) => within(li).getByRole("button", { name: `More options for ${name}` });

test("an item's name is a link to its own page", async () => {
  server([notebook]);
  const user = userEvent.setup();
  renderAt("/apps");
  const li = await row("Notebook");
  const name = within(li).getByRole("link", { name: "Notebook" });
  expect(name).toHaveAttribute("href", "/apps/p2");
  // Looks like a link without a pointer to hover over it.
  expect(name.className).toMatch(/\bunderline\b/);
  await user.click(name);
  expect(await screen.findByRole("heading", { level: 1, name: "Notebook" })).toBeInTheDocument();
});

test("every kind of item has the quiet menu, named for it, with Details and Remove from Bevro", async () => {
  server([ledger, notebook, briefing]);
  const user = userEvent.setup();
  renderAt("/apps");
  for (const p of [ledger, notebook, briefing]) {
    const li = await row(p.name);
    const button = more(li, p.name);
    expect(button).toBeVisible();
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button.textContent).toBe(""); // the dots are drawn; the name is spoken

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    const menu = within(li).getByRole("menu", { name: p.name });
    expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Details", "Remove from Bevro"]);
    expect(within(menu).getByRole("menuitem", { name: "Details" })).toHaveAttribute("href", `/apps/${p.id}`);
    await user.keyboard("{Escape}");
    expect(within(li).queryByRole("menu")).toBeNull();
  }
  // Nothing destructive sits on the rows themselves.
  expect(screen.queryByRole("button", { name: "Remove from Bevro" })).toBeNull();
});

test("Details in the menu opens that same item's page", async () => {
  server([ledger, notebook]);
  const user = userEvent.setup();
  renderAt("/apps");
  const li = await row("Notebook");
  await user.click(more(li, "Notebook"));
  await user.click(within(li).getByRole("menuitem", { name: "Details" }));
  expect(await screen.findByRole("heading", { level: 1, name: "Notebook" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { level: 1, name: "Ledger" })).toBeNull();
});

test("Remove asks first, in plain words, and Cancel keeps the item", async () => {
  const calls = server([notebook]);
  const user = userEvent.setup();
  renderAt("/apps");
  const li = await row("Notebook");
  await user.click(more(li, "Notebook"));
  await user.click(within(li).getByRole("menuitem", { name: "Remove from Bevro" }));

  const question = await within(li).findByRole("group", { name: "Remove Notebook from Bevro?" });
  expect(question).toHaveTextContent(
    "This removes Notebook from your Apps & agents list and removes Bevro's current setup for it. Your previous task history will remain.",
  );
  expect(question).toHaveTextContent("3 tasks stay in Recent");
  expect(question.textContent).not.toMatch(/delete (provider|runtime|connection|integration)|provider|runtime|adapter/i);
  expect(within(question).getAllByRole("button").map((b) => b.textContent)).toEqual(["Cancel", "Remove from Bevro"]);
  // The safe answer is the one under the finger.
  expect(within(question).getByRole("button", { name: "Cancel" })).toHaveFocus();

  await user.click(within(question).getByRole("button", { name: "Cancel" }));
  expect(within(li).queryByRole("group")).toBeNull();
  expect(screen.getByRole("heading", { level: 2, name: "Notebook" })).toBeInTheDocument();
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  expect(more(li, "Notebook")).toHaveFocus();
});

test("confirming removes it through Bevro's removal, it goes at once, and it stays gone", async () => {
  const calls = server([ledger, notebook, briefing]);
  const user = userEvent.setup();
  const first = renderAt("/apps");
  const li = await row("Briefing");
  await user.click(more(li, "Briefing"));
  await user.click(within(li).getByRole("menuitem", { name: "Remove from Bevro" }));
  const question = await within(li).findByRole("group", { name: "Remove Briefing from Bevro?" });
  await user.click(within(question).getByRole("button", { name: "Remove from Bevro" }));

  expect(calls.filter((c) => c.method === "DELETE").map((c) => c.url)).toEqual(["/api/providers/p3"]);
  const status = await screen.findByRole("status");
  expect(status).toHaveTextContent("Briefing was removed from Bevro.");
  expect(status).toHaveFocus();
  expect(screen.queryByRole("heading", { level: 2, name: "Briefing" })).toBeNull();
  expect(within(screen.getByRole("list", { name: "Apps and agents" })).getAllByRole("listitem")).toHaveLength(2);

  // A reload asks the server again, and it agrees.
  first.unmount();
  renderAt("/apps");
  expect(await screen.findByRole("heading", { level: 2, name: "Ledger" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { level: 2, name: "Briefing" })).toBeNull();
});

test("removing the last one leaves the empty page, with the ways to add something", async () => {
  server([ledger]);
  const user = userEvent.setup();
  renderAt("/apps");
  const li = await row("Ledger");
  await user.click(more(li, "Ledger"));
  await user.click(within(li).getByRole("menuitem", { name: "Remove from Bevro" }));
  await user.click(within(await within(li).findByRole("group")).getByRole("button", { name: "Remove from Bevro" }));
  expect(await screen.findByRole("heading", { name: "Your apps and agents will appear here." })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Connect something" })).toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "Apps and agents" })).toBeNull();
});

test("the menu closes on Escape, back on its button, and on a tap anywhere else", async () => {
  server([notebook]);
  const user = userEvent.setup();
  renderAt("/apps");
  const li = await row("Notebook");
  const button = more(li, "Notebook");

  await user.click(button);
  expect(within(li).getByRole("menu")).toBeInTheDocument();
  await user.keyboard("{Escape}");
  expect(within(li).queryByRole("menu")).toBeNull();
  expect(button).toHaveFocus();

  await user.click(button);
  expect(within(li).getByRole("menu")).toBeInTheDocument();
  await user.click(screen.getByRole("heading", { level: 1, name: "Apps & agents" }));
  expect(within(li).queryByRole("menu")).toBeNull();
  expect(button).toHaveAttribute("aria-expanded", "false");
});

test("the keyboard alone reaches the menu, Details, Remove, and the answer", async () => {
  const calls = server([notebook]);
  const user = userEvent.setup();
  renderAt("/apps");
  const li = await row("Notebook");
  // After the row's own actions, in reading order.
  within(li).getByRole("button", { name: "Set up direct access" }).focus();
  await user.tab();
  const button = more(li, "Notebook");
  expect(button).toHaveFocus();

  await user.keyboard("{Enter}");
  const details = within(li).getByRole("menuitem", { name: "Details" });
  const remove = within(li).getByRole("menuitem", { name: "Remove from Bevro" });
  expect(details).toHaveFocus();
  await user.keyboard("{ArrowDown}");
  expect(remove).toHaveFocus();
  await user.keyboard("{ArrowDown}");
  expect(details).toHaveFocus(); // round again, never stuck
  await user.keyboard("{ArrowUp}");
  expect(remove).toHaveFocus();

  // Tab leaves the menu and closes it; it is not a trap.
  await user.tab();
  expect(within(li).queryByRole("menu")).toBeNull();

  button.focus();
  await user.keyboard(" ");
  await user.keyboard("{End}");
  await user.keyboard("{Enter}");
  const question = await within(li).findByRole("group", { name: "Remove Notebook from Bevro?" });
  expect(within(question).getByRole("button", { name: "Cancel" })).toHaveFocus();
  await user.tab();
  expect(within(question).getByRole("button", { name: "Remove from Bevro" })).toHaveFocus();
  await user.keyboard("{Enter}");
  expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/providers/p2")).toBe(true);
  expect(await screen.findByText("Notebook was removed from Bevro.")).toBeInTheDocument();
});

test("on a phone the menu stays on screen: pinned to the row's corner, opening inward, never wider than the screen", async () => {
  server([briefing]);
  const user = userEvent.setup();
  renderAt("/apps");
  const li = await row("Briefing");
  const button = more(li, "Briefing");
  // A finger-sized target at every width.
  expect(button.className).toMatch(/min-h-\[44px\]/);
  expect(button.className).toMatch(/min-w-\[44px\]/);
  // In the corner, out of the flow: however the actions wrap, it cannot be pushed off the row.
  expect(button.closest(".absolute")).not.toBeNull();
  expect(within(li).getByRole("heading", { level: 2 }).parentElement!.className).toMatch(/\bpr-10\b/);
  await user.click(button);
  const menu = within(li).getByRole("menu");
  expect(menu.className).toMatch(/\bright-0\b/);
  expect(menu.className).toMatch(/max-w-\[calc\(100vw-2rem\)\]/);
});

test("the item's own page still removes it, with the same question", async () => {
  const calls = server([ledger]);
  const user = userEvent.setup();
  renderAt("/apps/p1");
  const care = await screen.findByRole("region", { name: "Settings" });
  const button = within(care).getByRole("button", { name: "Remove from Bevro" });
  await user.click(button);
  const question = within(care).getByRole("group", { name: "Remove Ledger from Bevro?" });
  await user.click(within(question).getByRole("button", { name: "Cancel" }));
  expect(within(care).getByRole("button", { name: "Remove from Bevro" })).toHaveFocus();
  await user.click(within(care).getByRole("button", { name: "Remove from Bevro" }));
  await user.click(within(within(care).getByRole("group", { name: "Remove Ledger from Bevro?" })).getByRole("button", { name: "Remove from Bevro" }));
  expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/providers/p1")).toBe(true);
  expect(await screen.findByRole("heading", { level: 1, name: "Apps & agents" })).toBeInTheDocument();
});
