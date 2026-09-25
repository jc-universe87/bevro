import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Help from "../components/Help";

test("Why help is optional, labelled and keyboard accessible", async () => {
  const user = userEvent.setup();
  render(<Help question="Why can't Bevro use this yet?">It has a screen for people, but no way for Bevro to send it a task.</Help>);

  const button = screen.getByRole("button", { name: "Why can't Bevro use this yet?" });
  const answer = screen.getByRole("note", { hidden: true });
  expect(button).toHaveAttribute("aria-expanded", "false");
  expect(button).toHaveAttribute("aria-controls", answer.id);
  expect(answer).not.toBeVisible();

  await user.tab();
  expect(button).toHaveFocus();
  await user.keyboard("{Enter}");
  expect(button).toHaveAttribute("aria-expanded", "true");
  expect(answer).toBeVisible();
});
