import { vi } from "vitest";

type Handler = (url: string, init?: RequestInit) => unknown;

/** Route fetch calls by "METHOD /api/path" to canned JSON. */
export function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const path = url.replace(/^https?:\/\/[^/]+/, "");
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, url: path, body });
      const key = Object.keys(routes).find((k) => {
        const [m, p] = k.split(" ");
        return m === method && (p === path || (p.endsWith("*") && path.startsWith(p.slice(0, -1))));
      });
      if (!key) return new Response(JSON.stringify({ detail: "no route" }), { status: 404 });
      const handler = routes[key];
      // A handler may answer later (a promise), to catch what the page shows meanwhile.
      const value = await (typeof handler === "function" ? (handler as Handler)(path, init) : handler);
      return new Response(JSON.stringify(value), { status: method === "POST" ? 201 : 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  return calls;
}

export const task = (over: Record<string, unknown> = {}) => ({
  id: "t1",
  title: "Move my meetings to free up Friday afternoon",
  original_request: "Move my meetings to free up Friday afternoon",
  state: "queued",
  summary: null,
  provider: { id: "p1", slug: "calendar-demo", name: "Calendar Demo" },
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  completed_at: null,
  runs: [],
  artifacts: [],
  input_request: null,
  ...over,
});
