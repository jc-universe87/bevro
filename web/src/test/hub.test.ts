/**
 * How an app or agent is presented: where a browser can open it, and the one
 * thing to do with it now. Synthetic shapes only.
 */
import { describe, expect, test } from "vitest";
import type { Provider, Surface } from "../lib/api";
import { TECHNICAL_WORDS } from "../lib/connectSteps";
import { availableThrough, browserAddress, hubView, openingHelp, seesTheMachineDirectly, type Here } from "../lib/hub";

const LAPTOP: Here = { hostname: "localhost", port: "6140" };
const PHONE_BY_IP: Here = { hostname: "100.64.0.7", port: "6140" };
const PHONE_BY_NAME: Here = { hostname: "box.example.ts.net", port: "6140" };
const BEHIND_PROXY: Here = { hostname: "bevro.example.org", port: "" };

const web = (url: string, reach: string): Surface => ({ kind: "web_app", role: "use", label: "Web app", sentence: "It has its own web app.", url, reach });

const item = (over: Partial<Provider> = {}): Provider => ({
  id: "p1",
  slug: "notebook",
  name: "Notebook",
  description: "Keeps what you have noted and learned.",
  enabled: true,
  capabilities: [],
  app_url: null,
  icon: null,
  origin: "connected",
  actions: [],
  connection: null,
  availability: { state: "unavailable", note: null },
  secret_names: [],
  credentials: [],
  runtime: null,
  surfaces: [],
  direct: { state: "not_set_up" },
  created_at: "",
  updated_at: "",
  ...over,
});

// --------------------------------------------------------------------------- 6, 7: browser addresses

describe("where a browser can open a web app", () => {
  test("an address given to Bevro, shared on a private network, or on a real network is used as it is", () => {
    for (const reach of ["explicit", "shared", "network"]) {
      expect(browserAddress(web("https://notes.example.org/", reach), PHONE_BY_IP)).toEqual({ kind: "link", href: "https://notes.example.org/" });
    }
  });

  test("a shared address, from this machine itself, goes straight to the app behind it", () => {
    const s = { ...web("https://box.example.ts.net:8443", "shared"), local_url: "http://127.0.0.1:6400" };
    expect(browserAddress(s, LAPTOP)).toEqual({ kind: "link", href: "http://127.0.0.1:6400/" });
    expect(browserAddress(s, PHONE_BY_NAME)).toEqual({ kind: "link", href: "https://box.example.ts.net:8443/" });
  });

  test("this machine only: a link only for a browser on this machine", () => {
    const s = web("http://127.0.0.1:6400/", "loopback");
    expect(browserAddress(s, LAPTOP)).toEqual({ kind: "link", href: "http://127.0.0.1:6400/" });
    expect(browserAddress(s, PHONE_BY_IP)).toEqual({ kind: "elsewhere", address: "http://127.0.0.1:6400/", reason: "this_computer" });
  });

  test("every interface: the host the browser used for Bevro, when it names the machine directly", () => {
    const s = web("http://127.0.0.1:8501/", "all_interfaces");
    expect(browserAddress(s, PHONE_BY_IP)).toEqual({ kind: "link", href: "http://100.64.0.7:8501/" });
    expect(browserAddress(s, PHONE_BY_NAME)).toEqual({ kind: "link", href: "http://box.example.ts.net:8501/" });
    expect(browserAddress(s, LAPTOP)).toEqual({ kind: "link", href: "http://127.0.0.1:8501/" });
  });

  test("behind a reverse proxy the name is the proxy's, so no link is made up", () => {
    expect(seesTheMachineDirectly(BEHIND_PROXY)).toBe(false);
    expect(browserAddress(web("http://127.0.0.1:8501/", "all_interfaces"), BEHIND_PROXY)).toEqual({ kind: "elsewhere", address: "http://127.0.0.1:8501/", reason: "unknown_host" });
  });

  test("anything that is not a web address is never a link", () => {
    expect(browserAddress(web("javascript:alert(1)", "explicit"), LAPTOP)).toBeNull();
    expect(browserAddress(web("not a url", "network"), LAPTOP)).toBeNull();
    expect(browserAddress(undefined, LAPTOP)).toBeNull();
  });

  test("instead of a broken link, it says where it opens", () => {
    const help = openingHelp("Notebook", browserAddress(web("http://127.0.0.1:6400/", "loopback"), PHONE_BY_IP));
    expect(help).toMatch(/only opens on the computer it runs on, at http:\/\/127\.0\.0\.1:6400/);
    expect(openingHelp("Notebook", { kind: "link", href: "https://x" })).toBeNull();
  });
});

describe("choosing among several addresses for the browser in front of it", () => {
  // Shapes only: a machine at 100.64.0.9 / box.example.ts.net, sharing an app
  // published on its own port 6400 over HTTPS (8443) and a plain forward (6401).
  const many = (): Surface => ({
    kind: "web_app",
    role: "use",
    label: "Web app",
    sentence: "",
    url: "https://box.example.ts.net:8443",
    reach: "shared",
    candidates: [
      { url: "https://box.example.ts.net:8443", reach: "shared" },
      { url: "http://box.example.ts.net:6401", reach: "shared" },
      { url: "http://100.64.0.9:6401", reach: "shared" },
      { url: "http://127.0.0.1:6400", reach: "loopback" },
    ],
  });
  const SAME_MACHINE_BY_IP: Here = { hostname: "100.64.0.9", port: "6140" };
  const BY_NAME: Here = { hostname: "box.example.ts.net", port: "6140" };
  const OTHER_DEVICE: Here = { hostname: "bevro.example.org", port: "" };
  const href = (s: Surface, at: Here) => {
    const o = browserAddress(s, at);
    return o?.kind === "link" ? o.href : o;
  };

  test("A: Bevro opened at localhost, app on this machine: the local address", () => {
    expect(href(many(), LAPTOP)).toBe("http://127.0.0.1:6400/");
    expect(href({ ...web("http://127.0.0.1:6400/", "loopback"), candidates: [{ url: "http://127.0.0.1:6400/", reach: "loopback" }] }, { hostname: "127.0.0.1", port: "6140" })).toBe("http://127.0.0.1:6400/");
  });

  test("B, G: Bevro opened at this machine's IP address: the app on that same address, not a name it may not resolve", () => {
    expect(href(many(), SAME_MACHINE_BY_IP)).toBe("http://100.64.0.9:6401/");
    // Published on every interface: the IP it was opened at, with the app's own port.
    expect(href({ ...web("http://127.0.0.1:8501", "all_interfaces"), candidates: [{ url: "http://127.0.0.1:8501", reach: "all_interfaces" }] }, SAME_MACHINE_BY_IP)).toBe("http://100.64.0.9:8501/");
  });

  test("H: Bevro opened by the machine's name: the addresses under that name, HTTPS first", () => {
    expect(href(many(), BY_NAME)).toBe("https://box.example.ts.net:8443/");
  });

  test("C: another device, an app only this machine can open: no link, and where it opens instead", () => {
    const local = { ...web("http://127.0.0.1:6400", "loopback"), candidates: [{ url: "http://127.0.0.1:6400", reach: "loopback" }] };
    expect(browserAddress(local, OTHER_DEVICE)).toEqual({ kind: "elsewhere", address: "http://127.0.0.1:6400/", reason: "this_computer" });
    expect(hubView(item({ surfaces: [local] }), OTHER_DEVICE).primary.id).toBe("how_to_open");
  });

  test("D: another device, an app shared on the private network: the shared address, never 127.0.0.1", () => {
    expect(href(many(), OTHER_DEVICE)).toBe("https://box.example.ts.net:8443/");
    expect(href(many(), PHONE_BY_IP)).toBe("http://100.64.0.9:6401/"); // same sort of address as the browser used
  });

  test("E: an address the person gave is kept - unless this browser can't possibly use it", () => {
    const given = (url: string): Surface => ({ ...many(), candidates: [{ url, reach: "explicit" }, ...many().candidates!] });
    expect(href(given("https://notes.example.org/"), SAME_MACHINE_BY_IP)).toBe("https://notes.example.org/");
    expect(href(given("http://127.0.0.1:9999/"), SAME_MACHINE_BY_IP)).toBe("http://100.64.0.9:6401/");
    expect(href(given("http://127.0.0.1:9999/"), LAPTOP)).toBe("http://127.0.0.1:9999/");
  });

  test("F: the same inputs always give the same answer, whatever order equals arrive in", () => {
    const a = many();
    const b = { ...a, candidates: [...a.candidates!].reverse() };
    for (const at of [LAPTOP, SAME_MACHINE_BY_IP, BY_NAME, OTHER_DEVICE]) {
      expect(href(a, at)).toBe(href(a, at));
    }
    // Different tiers do not depend on order; only equals fall back to the server's order.
    expect(href(b, SAME_MACHINE_BY_IP)).toBe("http://100.64.0.9:6401/");
    expect(href(b, LAPTOP)).toBe("http://127.0.0.1:6400/");
  });

  test("I: scheme, port and path survive, including a substituted host", () => {
    expect(href({ ...web("https://127.0.0.1:8443/app/?x=1", "all_interfaces"), candidates: [{ url: "https://127.0.0.1:8443/app/?x=1", reach: "all_interfaces" }] }, BY_NAME)).toBe("https://box.example.ts.net:8443/app/?x=1");
    expect(href({ ...web("https://box.example.ts.net/wiki", "shared"), candidates: [{ url: "https://box.example.ts.net/wiki", reach: "shared" }] }, OTHER_DEVICE)).toBe("https://box.example.ts.net/wiki");
  });

  test("an address stored before there were lists still works", () => {
    const old: Surface = { ...web("https://box.example.ts.net:8443", "shared"), local_url: "http://127.0.0.1:6400" };
    expect(href(old, LAPTOP)).toBe("http://127.0.0.1:6400/");
    expect(href(old, OTHER_DEVICE)).toBe("https://box.example.ts.net:8443/");
  });
});

// --------------------------------------------------------------------------- 1-5, 8-11: what it is and what to do

describe("the one thing to do with it", () => {
  test("1: Bevro can send it work: Use in Bevro", () => {
    const v = hubView(item({ actions: ["ask"], direct: { state: "ready" } }), LAPTOP);
    expect(v.primary).toEqual({ id: "use", label: "Use in Bevro" });
    expect(v.through).toEqual(["Bevro"]);
    expect(v.attention).toBeNull();
  });

  test("2, 9: a web app on its own is opened, and nothing about it is a problem", () => {
    const v = hubView(item({ actions: ["open"], surfaces: [web("https://notes.example.org/", "shared")] }), PHONE_BY_IP);
    expect(v.primary).toEqual({ id: "open", label: "Open Notebook", href: "https://notes.example.org/" });
    expect(v.through).toEqual(["Web app"]);
    expect(v.attention).toBeNull();
  });

  test("3: both: use it in Bevro, open the app quietly", () => {
    const v = hubView(item({ actions: ["ask", "open"], direct: { state: "ready" }, surfaces: [web("http://10.0.0.8/app", "network")] }), PHONE_BY_IP);
    expect(v.primary.id).toBe("use");
    expect(v.secondary).toEqual([{ id: "open", label: "Open app", href: "http://10.0.0.8/app" }]);
    expect(v.through).toEqual(["Bevro", "Web app"]);
  });

  test("4: results it posts somewhere and a schedule are said, and never as a way in", () => {
    const p = item({
      direct: { state: "needs_credential" },
      surfaces: [
        { kind: "telegram", role: "delivers", label: "Telegram", sentence: "It sends its results to Telegram." },
        { kind: "schedule", role: "runs", label: "Scheduled runs", sentence: "It runs by itself, every Monday at 07:30.", when: "every Monday at 07:30", installed: true },
        { kind: "command_line", role: "use", label: "Command line", sentence: "It can be run from the command line on this machine." },
      ],
    });
    const v = hubView(p, LAPTOP);
    expect(v.through).toEqual(["Scheduled runs", "Results to Telegram"]);
    expect(v.primary).toEqual({ id: "add_credential", label: "Add credential" });
    expect(v.secondary).toEqual([{ id: "how_to", label: "How to use it" }]);
    // The app is fine; only direct use lacks something. Said, not warned about.
    expect(v.attention).toBeNull();
    expect(v.directNote).toBe("Direct use in Bevro needs a credential");
  });

  test("5: a way in that stopped working is a problem, with Try again", () => {
    const v = hubView(item({ direct: { state: "unreachable" } }), LAPTOP);
    expect(v.primary).toEqual({ id: "retry", label: "Try again" });
    expect(v.attention).toBe("Can't be reached right now");
  });

  test("7: a web app this browser can't reach says how to open it rather than linking", () => {
    const v = hubView(item({ surfaces: [web("http://127.0.0.1:6400/", "loopback")] }), PHONE_BY_IP);
    expect(v.primary).toEqual({ id: "how_to_open", label: "How to open Notebook" });
    expect(v.opening?.kind).toBe("elsewhere");
  });

  test("10: setting up direct access is secondary whenever something else is useful", () => {
    const withApp = hubView(item({ surfaces: [web("https://n.example", "explicit")] }), LAPTOP);
    expect(withApp.primary.id).toBe("open");
    expect(withApp.secondary).toEqual([{ id: "setup_direct", label: "Set up direct access" }]);
    const unreachableWithApp = hubView(item({ direct: { state: "unreachable" }, surfaces: [web("https://n.example", "explicit")] }), LAPTOP);
    expect(unreachableWithApp.primary.id).toBe("open");
    expect(unreachableWithApp.secondary.map((a) => a.id)).toEqual(["retry"]);
    // Only when nothing else is known is it the thing to do.
    expect(hubView(item(), LAPTOP).primary).toEqual({ id: "setup_direct", label: "Set up direct access" });
  });

  test("paused is said, and resuming is the thing to do", () => {
    const v = hubView(item({ enabled: false, actions: [], direct: { state: "paused" } }), LAPTOP);
    expect(v.primary).toEqual({ id: "resume", label: "Resume" });
    expect(v.attention).toBeNull();
  });

  test("11: nothing a person reads here names the machinery", () => {
    const states = ["ready", "needs_credential", "waiting_for_worker", "needs_start", "unreachable", "paused", "not_set_up"] as const;
    for (const state of states) {
      for (const surfaces of [[], [web("http://127.0.0.1:1/", "loopback")], [web("https://x.example", "explicit")]]) {
        const v = hubView(item({ direct: { state }, surfaces, actions: state === "ready" ? ["ask"] : [] }), PHONE_BY_IP);
        const words = [v.primary.label, ...v.secondary.map((a) => a.label), v.attention ?? "", ...v.through, openingHelp("Notebook", v.opening) ?? ""].join(" ");
        // An address is what the person needs to open it; the words around it are what matter.
        expect(words.replace(/https?:\/\/\S+/g, "")).not.toMatch(TECHNICAL_WORDS);
      }
    }
  });

  test("a command line is mentioned only when nothing friendlier is known", () => {
    const cli: Surface = { kind: "command_line", role: "use", label: "Command line", sentence: "" };
    expect(availableThrough(item({ surfaces: [cli] }), false)).toEqual(["Command line"]);
    expect(availableThrough(item({ surfaces: [cli, web("https://x", "explicit")] }), true)).toEqual(["Bevro", "Web app"]);
  });
});
