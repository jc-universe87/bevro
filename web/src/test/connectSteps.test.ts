import { describe, expect, test } from "vitest";
import type { ConnectDraft, DraftView } from "../lib/api";
import { connectStep, stepProblems, type Step } from "../lib/connectSteps";

const view = (over: Partial<DraftView> = {}): DraftView => ({
  runs_via: "Runs from this project",
  runs_at: "On this machine",
  runtime: null,
  runtime_options: [],
  runtimes_found: 1,
  choice_needed: false,
  scope_choices: [],
  credentials_label: "None needed",
  name: "Archivist",
  description: "Searches and organises documents.",
  capabilities: [{ id: "search_documents", title: "Search documents" }],
  mechanism: "local",
  mechanism_label: "Local service",
  invocation_label: null,
  availability: "ready",
  confidence: "high",
  confidence_label: "Confident",
  note: null,
  needs_description: false,
  described: false,
  evidence: [],
  warnings: [],
  app_url: null,
  auth: { required: false, secret_name: null, label: null, hint: null, why: null },
  invocable: true,
  ...over,
});

const draft = (over: Partial<ConnectDraft> = {}): ConnectDraft => ({
  id: "d1",
  state: "found",
  target_kind: "local",
  target_label: "Archivist",
  draft: view(),
  error: null,
  test: null,
  provider_id: null,
  created_at: "",
  ...over,
});

function step(over: Partial<ConnectDraft> = {}, local = {}): Step {
  const result = connectStep(draft(over), local);
  if (!result) throw new Error("expected a step");
  return result;
}

test("the primary action follows the first unresolved fact", () => {
  expect(step().primary).toEqual({ id: "connect", label: "Connect" });

  const description = step({ draft: view({ needs_description: true, capabilities: [], description: "", confidence: "low" }) });
  expect(description).toMatchObject({ kind: "needs_description", input: "description", primary: { id: "describe", label: "Continue" } });

  const noInterface = step({ draft: view({ invocable: false, availability: "not_invocable" }) });
  expect(noInterface.primary).toEqual({ id: "setup", label: "Set up how to use it" });

  const credential = step({ draft: view({ auth: { required: true, secret_name: "OPENAI_API_KEY", label: "OpenAI credential", hint: null, why: null } }) });
  expect(credential).toMatchObject({ kind: "needs_credential", input: "credential", primary: { id: "add_credential", label: "Add credential" }, secondary: { id: "test", label: "Test" } });

  expect(step({ state: "trust_required", draft: null, trust: { kind: "folder", label: "Archivist", path: "/allowed/archivist" } }).primary).toEqual({ id: "allow", label: "Allow folder" });
  expect(step({ state: "choice_required", draft: null, choices: [{ label: "archivist", where: "~/agents/archivist" }] }).primary).toEqual({ id: "choose", label: "Choose" });
  expect(step({ state: "failed", draft: null, problem: "worker", error: "The worker did not answer." }).primary).toEqual({ id: "retry", label: "Try again" });
  expect(step({ already_connected: { id: "p1", name: "Archivist" } }).primary).toEqual({ id: "open", label: "Open in Agents" });
});

test("a failed granular test replaces a misleading Connect action with recovery", () => {
  const result = step({
    test: {
      ok: false,
      detail: "It can't take work yet.",
      checks: [{ label: "The command Bevro would use isn't there", ok: false }],
      next: null,
    },
  });
  expect(result.primary).toEqual({ id: "setup", label: "Set up how to use it" });
  expect(result.secondary).toEqual({ id: "test", label: "Try again" });
});

test("plain credential copy explains an existing scheduled credential without system jargon", () => {
  const result = step({
    draft: view({
      name: "Moimio Research",
      auth: {
        required: true,
        secret_name: "OPENAI_API_KEY",
        label: "OpenAI credential",
        hint: "Moimio Research already has a credential for its scheduled runs, but that credential isn't available when Bevro starts a new task.",
        why: "The credential is handed over only when its scheduled service starts. When Bevro starts a task, it runs Moimio Research separately, so the credential doesn't reach it.",
      },
    }),
  });
  expect(result.message).toMatch(/scheduled runs/);
  expect(result.help?.question).toBe("Why can't Bevro use the existing one?");
  expect(`${result.heading} ${result.message}`).not.toMatch(/systemd|EnvironmentFile|runtime/i);
});

describe("every Connect state has one safe way forward", () => {
  const states: [string, Step][] = [
    ["resolving", step({ state: "looking", target_kind: "name" })],
    ["discovering", step({ state: "looking", target_kind: "url" })],
    ["choice required", step({ state: "choice_required", draft: null, choices: [{ label: "A", where: "~/A" }] })],
    ["trust required", step({ state: "trust_required", draft: null, trust: { kind: "folder", label: "A" } })],
    ["testing", step({ state: "testing" })],
    ["ready", step()],
    ["description", step({ draft: view({ needs_description: true, capabilities: [], description: "" }) })],
    ["credential", step({ draft: view({ auth: { required: true, secret_name: "api_key", label: "API token", hint: null, why: null } }) })],
    ["interface", step({ draft: view({ invocable: false, availability: "not_invocable" }) })],
    ["start", step({ draft: view({ invocable: false, availability: "needs_start" }) })],
    ["scope choice", step({ draft: view({ scope_choices: [{ value: "one", label: "One" }] }) })],
    ["building", step({}, { bridge: { state: "building", note: "Building connection…", provider_id: "p1", task_id: "t1", steps: [] } })],
    ["built", step({}, { bridge: { state: "ready", note: "Ready", provider_id: "p1", task_id: "t1", steps: [] } })],
    ["connected", step({ state: "connected" })],
    ["worker unavailable", step({ state: "failed", draft: null, problem: "worker", error: "Unavailable" })],
    ["not found", step({ state: "failed", draft: null, problem: "not_found", error: "Nothing found" })],
    ["unreachable", step({ state: "failed", draft: null, problem: "unreachable", error: "It did not answer" })],
    ["failed", step({ state: "failed", draft: null, problem: "failed", error: "Something went wrong" })],
    ["already connected", step({ already_connected: { id: "p1", name: "Archivist" } })],
    [
      "two ways",
      step({
        draft: view({
          choice_needed: true,
          runtime_options: [
            { id: "a", display_name: "A", credentials: { label: "None needed" } },
            { id: "b", display_name: "B", credentials: { label: "Missing" } },
          ] as DraftView["runtime_options"],
        }),
      }),
    ],
    ["credential added", step({ draft: view({ auth: { required: true, secret_name: "api_key", label: "API token", hint: null, why: null } }) }, { credentialAdded: true })],
    ["test failed", step({ test: { ok: false, detail: "It can't take work yet.", checks: [{ label: "It didn't answer", ok: false }], next: null } })],
    [
      "test wants a credential",
      step({
        draft: view({ auth: { required: true, secret_name: "api_key", label: "API token", hint: null, why: null } }),
        test: { ok: false, detail: "Needs a credential.", checks: [{ label: "No API token yet", ok: false, kind: "credential" }], next: "add_credential" },
      }),
    ],
    ["bridge failed", step({}, { bridge: { state: "failed", note: "Building didn't work.", provider_id: "p1", task_id: "t1", steps: [] } })],
    ["lost draft", step({ draft: null })],
    ["website only", step({ draft: view({ invocable: false, availability: "not_invocable", needs_description: true, web_ui: { running: true, title: null, routes: ["/api"] } }) })],
  ];

  test.each(states)("%s", (_name, current) => {
    expect(stepProblems(current)).toEqual([]);
    if (!current.busy) expect(current.primary).toBeDefined();
  });
});

describe("a website is not a way to send work", () => {
  const websiteOnly = (over: Partial<DraftView> = {}) =>
    view({ name: "Notebook", invocable: false, availability: "not_invocable", web_ui: { running: true, title: "Notebook", routes: ["/api"] }, ...over });

  test("says what was found, and offers setup - before asking what it is for", () => {
    const s = step({ draft: websiteOnly({ needs_description: true, capabilities: [], confidence: "low" }) });
    expect(s).toMatchObject({ kind: "needs_interface", heading: "Notebook is running on this machine.", primary: { id: "setup", label: "Set up how to use it" } });
    expect(s.message).toMatch(/only has its own website/);
    expect(s.help?.answer).toMatch(/hasn't found a safe way for another program to send it work/);
    expect(stepProblems(s)).toEqual([]);
  });

  test("normal copy never names the machinery behind it", () => {
    const s = step({ draft: websiteOnly() });
    const said = `${s.heading} ${s.message} ${s.help?.question} ${s.help?.answer} ${s.primary?.label}`;
    expect(said).not.toMatch(/fastapi|openapi|nginx|proxy|cookie|session|docker|compose|port|\/api/i);
  });

  test("an address that is only a website says running, not on this machine", () => {
    expect(step({ draft: websiteOnly({ mechanism: "http" }) }).heading).toBe("Notebook is running.");
  });

  test("a usable way in wins over the website being there", () => {
    expect(step({ draft: websiteOnly({ invocable: true, availability: "ready" }) }).kind).toBe("found_ready");
  });

  test("a building agent is offered when one could build a connection", () => {
    const s = step({ draft: websiteOnly({ needs_bridge: true, bridge_possible: true }) });
    expect(s.primary).toEqual({ id: "build", label: "Set up how to use it" });
    expect(s.tertiary).toEqual([{ id: "setup", label: "Set it up by hand" }]);
  });
});
