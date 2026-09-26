import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi, task } from "./helpers";

test("recent is a list with title, provider, state and summary, and search hits the API", async () => {
  const calls = mockApi({
    "GET /api/tasks*": (url: string) =>
      url.includes("q=friday")
        ? [task({ state: "completed", summary: "Done. 3 meetings moved. 1 needs your reply." })]
        : [task({ state: "completed", summary: "Done. 3 meetings moved. 1 needs your reply." }), task({ id: "t2", title: "Compare note apps", provider: { id: "p2", slug: "research", name: "Research" }, state: "working" })],
    "GET /api/providers": [],
  });
  const user = userEvent.setup();
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/recent"]}>
      <App />
    </MemoryRouter>,
  );
  const list = await screen.findByRole("list", { name: "Recent work" });
  const items = await screen.findAllByRole("listitem");
  expect(items).toHaveLength(2);
  expect(list).toHaveTextContent("Move my meetings to free up Friday afternoon");
  expect(list).toHaveTextContent("Calendar Demo");
  expect(list).toHaveTextContent("Done. 3 meetings moved. 1 needs your reply.");
  expect(list).toHaveTextContent("Research");
  expect(list).toHaveTextContent("Working…");

  await user.type(screen.getByRole("searchbox", { name: "Search recent work" }), "friday");
  await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(1));
  expect(calls.some((c) => c.url.includes("q=friday"))).toBe(true);
});
