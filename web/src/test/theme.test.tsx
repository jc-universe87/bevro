import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { mockApi } from "./helpers";

test("settings switches the theme on the document root and remembers it", async () => {
  mockApi({ "GET /api/meta": { name: "Bevro", version: "0.1.0", tagline: "" }, "GET /api/tasks": [], "GET /api/providers": [], "GET /api/workspaces": [] });
  const user = userEvent.setup();
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }} initialEntries={["/settings"]}>
      <App />
    </MemoryRouter>,
  );
  await user.click(await screen.findByRole("radio", { name: "Dark" }));
  expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  expect(localStorage.getItem("bevro.theme")).toBe("dark");
  await user.click(screen.getByRole("radio", { name: "System" }));
  expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  expect(localStorage.getItem("bevro.theme")).toBeNull();
});
