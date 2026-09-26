import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ArtifactView from "../components/ArtifactView";
import type { Artifact } from "../lib/api";

const base: Artifact = {
  id: "a", task_id: "t", provider_run_id: null, type: "note", title: "T", summary: null, mime_type: null,
  payload: null, external_url: null, content_url: null, metadata: {}, known: true, created_at: "",
};

test("unknown artifact types fall back to Open result", () => {
  render(<ArtifactView artifact={{ ...base, type: "hologram", title: "A hologram", external_url: "https://example.com/h", known: false }} />);
  const link = screen.getByRole("link", { name: /Open result/ });
  expect(link).toHaveAttribute("href", "https://example.com/h");
  expect(link).toHaveAttribute("target", "_blank");
});

test("unknown type without anywhere to open says so plainly", () => {
  render(<ArtifactView artifact={{ ...base, type: "hologram", known: false }} />);
  expect(screen.getByText("This result can't be shown here yet.")).toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});

test("notes render their text and structured data renders as a table", () => {
  const { rerender } = render(<ArtifactView artifact={{ ...base, payload: { text: "# Findings\nHello" } }} />);
  expect(screen.getByText(/Hello/)).toBeInTheDocument();
  rerender(<ArtifactView artifact={{ ...base, type: "structured", title: "Your week", payload: { columns: ["Change", "Count"], rows: [["Meetings moved", 3], ["Waiting for your reply", 1]] } }} />);
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getByText("3")).toBeInTheDocument();
});

test("a very long result opens to a readable amount, with the rest a click away", async () => {
  const user = userEvent.setup();
  const long = "word ".repeat(2000);  // ~10k characters
  render(<ArtifactView artifact={{ ...base, title: "Report", payload: { text: long } }} />);

  const shown = () => screen.getByText(/word/).textContent ?? "";
  expect(shown().length).toBeLessThan(long.length);
  expect(shown()).toMatch(/…$/);

  await user.click(screen.getByRole("button", { name: /Show all/ }));
  expect(screen.getByText(/word/).textContent?.length).toBe(long.length);
  await user.click(screen.getByRole("button", { name: "Show less" }));
  expect(shown()).toMatch(/…$/);
});

test("a long table shows the first rows and offers the rest", async () => {
  const user = userEvent.setup();
  const rows = Array.from({ length: 60 }, (_, i) => [`Row ${i}`, i]);
  render(<ArtifactView artifact={{ ...base, type: "structured", title: "Everything", payload: { columns: ["Name", "n"], rows } }} />);

  expect(screen.getAllByRole("row")).toHaveLength(26);  // 25 rows plus the header
  expect(screen.queryByText("Row 40")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Show all 60 rows" }));
  expect(screen.getByText("Row 40")).toBeInTheDocument();
});

test("structured data that is not a table is still readable", () => {
  render(<ArtifactView artifact={{ ...base, type: "structured", title: "Summary", payload: { moved: 3, needs_reply: 1, notes: ["a", "b"] } }} />);
  expect(screen.getByText("moved")).toBeInTheDocument();
  expect(screen.getByText("3")).toBeInTheDocument();
  expect(screen.getByText("needs reply")).toBeInTheDocument();
  expect(screen.getByText("2 items")).toBeInTheDocument();
  // No raw JSON anywhere.
  expect(screen.queryByText(/[{}]/)).not.toBeInTheDocument();
});
