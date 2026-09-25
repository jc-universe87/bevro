/// <reference types="vite/client" />
import { expect, test } from "vitest";

// The real projects Connect was accepted against. Their shapes live in test
// fixtures; their names belong nowhere in what ships.
const ACCEPTANCE_PROJECTS = /\b(zekor|archivist|moimio)\b/i;

const shipped = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../test/**"], { query: "?raw", import: "default", eager: true });

test("no acceptance project is named in shipped web code", () => {
  expect(Object.keys(shipped).length).toBeGreaterThan(10);
  const offenders = Object.entries(shipped).flatMap(([path, text]) =>
    text
      .split("\n")
      .filter((line) => ACCEPTANCE_PROJECTS.test(line))
      .map((line) => `${path}: ${line.trim()}`),
  );
  expect(offenders).toEqual([]);
});
