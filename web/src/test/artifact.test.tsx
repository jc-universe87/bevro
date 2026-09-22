import { render, screen } from "@testing-library/react";
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
  rerender(<ArtifactView artifact={{ ...base, type: "structured", title: "Allocation summary", payload: { columns: ["Outcome", "Count"], rows: [["Allocated", 148], ["Need review", 7]] } }} />);
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getByText("148")).toBeInTheDocument();
});
