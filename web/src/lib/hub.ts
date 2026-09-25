import type { Provider, Surface } from "./api";

/**
 * How an app or agent is presented: how it can be used, and the one thing
 * to do with it now. See docs/HUB.md.
 *
 * Three answers are kept apart here, as on the server: Bevro knows it (it is
 * listed at all), the person uses it somewhere (its surfaces), and Bevro can
 * send it work (`direct`). An app with its own website and nothing for
 * programs is complete and healthy; nothing here treats it as a fault.
 */

/** Where this browser is, as far as opening an app is concerned. */
export interface Here {
  hostname: string;
  /** "" when the address had no explicit port. */
  port: string;
}

export function here(): Here {
  return { hostname: window.location.hostname, port: window.location.port };
}

/** A link the browser can follow, or where the app can be opened instead. */
export type Opening =
  | { kind: "link"; href: string }
  | {
      kind: "elsewhere";
      /** The address that does work, somewhere else. */
      address: string;
      /** this_computer: only a browser on the machine it runs on can open it.
       *  unknown_host: it can be opened from other devices, but Bevro can't tell by which name. */
      reason: "this_computer" | "unknown_host";
    };

const LOOPBACK = /^(localhost|.*\.localhost|127(\.\d{1,3}){3}|\[?::1\]?)$/i;
const IP_LITERAL = /^(\d{1,3}(\.\d{1,3}){3}|\[?[0-9a-f:]+\]?)$/i;

export function isLoopbackHost(hostname: string): boolean {
  return LOOPBACK.test(hostname);
}

/**
 * Is this browser looking at Bevro's machine directly, by a name that names
 * the machine? An IP address, a localhost name or an explicit port says so.
 * Behind a reverse proxy on the default port the name belongs to the proxy,
 * and putting another port on it would produce a link that does not work.
 */
export function seesTheMachineDirectly(at: Here): boolean {
  return isLoopbackHost(at.hostname) || IP_LITERAL.test(at.hostname) || at.port !== "";
}

/**
 * Where this browser can open a web app from, given how its address was
 * published. Runtime reachability (the worker can reach it) says nothing
 * about this: a service on 127.0.0.1 answers the worker and fails on a phone.
 */
export function browserAddress(surface: Surface | undefined, at: Here): Opening | null {
  if (!surface?.url) return null;
  let url: URL;
  try {
    url = new URL(surface.url);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  switch (surface.reach) {
    case "shared":
      // A browser on this machine goes straight to the app, rather than
      // relying on it resolving the private network's name for itself.
      if (isLoopbackHost(at.hostname) && surface.local_url) return { kind: "link", href: new URL(surface.local_url).toString() };
      return { kind: "link", href: url.toString() };
    case "explicit":
    case "network":
      return { kind: "link", href: url.toString() };
    case "all_interfaces":
      // Published on every interface of Bevro's machine: the name this
      // browser used for that machine reaches it too.
      if (isLoopbackHost(at.hostname)) return { kind: "link", href: url.toString() };
      if (!seesTheMachineDirectly(at)) return { kind: "elsewhere", address: url.toString(), reason: "unknown_host" };
      url.hostname = at.hostname;
      return { kind: "link", href: url.toString() };
    default:
      // This machine only (or not known to be anything more).
      return isLoopbackHost(at.hostname) ? { kind: "link", href: url.toString() } : { kind: "elsewhere", address: url.toString(), reason: "this_computer" };
  }
}

export type HubActionId = "use" | "open" | "add_credential" | "retry" | "setup_direct" | "how_to" | "how_to_open" | "resume";

export interface HubAction {
  id: HubActionId;
  label: string;
  /** For "open": where it goes. */
  href?: string;
}

export interface HubView {
  /** "Available through": short labels, most useful first. Empty when nothing is known. */
  through: string[];
  /** The one thing to do with it now. */
  primary: HubAction;
  /** Quiet alternatives. */
  secondary: HubAction[];
  /** A real problem, in a few words. Null when nothing is wrong - including when Bevro simply can't drive it. */
  attention: string | null;
  /** Something true about using it directly in Bevro that is not a fault of the app: said plainly, never as a warning. */
  directNote: string | null;
  /** Work under way on it (a new version being built): neither good nor bad news. */
  progress: string | null;
  web: Surface | undefined;
  opening: Opening | null;
  /** Bevro can send it work right now. */
  usable: boolean;
}

// Real problems: something that should work doesn't. A missing credential
// is not one of them - the app is fine; Bevro just hasn't been given what it
// needs to use it directly.
const PROBLEMS: Record<string, string> = {
  unreachable: "Can't be reached right now",
  waiting_for_worker: "Waiting for this computer",
  needs_start: "Not running",
};

function directState(p: Provider): string {
  if (p.direct?.state) return p.direct.state;
  return p.actions.includes("ask") ? "ready" : "not_set_up";
}

/** The short labels for "Available through". */
export function availableThrough(p: Provider, usable: boolean): string[] {
  const out: string[] = [];
  if (usable) out.push("Bevro");
  const surfaces = p.surfaces ?? [];
  for (const s of surfaces.filter((x) => x.role === "use" && x.kind !== "command_line")) out.push(s.label);
  if (surfaces.some((x) => x.role === "runs" && x.installed !== false)) out.push("Scheduled runs");
  for (const s of surfaces.filter((x) => x.role === "delivers")) out.push(`Results to ${s.label}`);
  // A command line is a way in for someone at the machine; said only when there is nothing friendlier.
  if (!out.length && surfaces.some((x) => x.kind === "command_line")) out.push("Command line");
  return [...new Set(out)];
}

export function hubView(p: Provider, at: Here): HubView {
  const state = directState(p);
  const usable = p.enabled && state === "ready" && p.actions.includes("ask");
  const web = (p.surfaces ?? []).find((s) => s.kind === "web_app");
  const opening = browserAddress(web, at);
  const open: HubAction | null = opening?.kind === "link" ? { id: "open", label: `Open ${p.name}`, href: opening.href } : null;
  const openQuiet: HubAction | null = opening?.kind === "link" ? { id: "open", label: "Open app", href: opening.href } : null;
  const howToOpen: HubAction | null = web && !open ? { id: "how_to_open", label: `How to open ${p.name}` } : null;
  const problem = p.enabled ? (PROBLEMS[state] ?? null) : null;
  const building = p.build && p.build.state !== "ready" ? (p.build.state === "failed" ? "Couldn't be built" : null) : null;
  const view = (primary: HubAction, secondary: (HubAction | null)[]): HubView => ({
    through: availableThrough(p, usable),
    primary,
    secondary: secondary.filter((a): a is HubAction => a !== null && a.id !== primary.id),
    attention: building ?? problem,
    directNote: p.enabled && state === "needs_credential" ? "Direct use in Bevro needs a credential" : null,
    progress: p.build && p.build.state !== "ready" && p.build.state !== "failed" ? "Building a new version" : null,
    web,
    opening,
    usable,
  });

  if (!p.enabled) return view({ id: "resume", label: "Resume" }, [open ? openQuiet : null]);
  if (usable) return view({ id: "use", label: "Use in Bevro" }, [openQuiet]);

  // Bevro can't send it work. Whatever else it offers comes first; setting
  // up direct access is always there, and always quiet.
  const fix: HubAction | null =
    state === "needs_credential"
      ? { id: "add_credential", label: "Add credential" }
      : state === "unreachable" || state === "waiting_for_worker" || state === "needs_start"
        ? { id: "retry", label: "Try again" }
        : null;
  const setup: HubAction = { id: "setup_direct", label: "Set up direct access" };
  if (open) return view(open, [fix ?? setup]);
  if (fix) return view(fix, [howToOpen, { id: "how_to", label: "How to use it" }]);
  if (howToOpen) return view(howToOpen, [setup]);
  if ((p.surfaces ?? []).length) return view({ id: "how_to", label: "How to use it" }, [setup]);
  return view(setup, []);
}

/** Where an app can be opened when this browser can't follow a link to it. */
export function openingHelp(name: string, opening: Opening | null): string | null {
  if (!opening || opening.kind !== "elsewhere") return null;
  const address = opening.address.replace(/\/$/, "");
  return opening.reason === "this_computer"
    ? `${name} only opens on the computer it runs on, at ${address}. To open it from here, give Bevro the address you use for it on this device.`
    : `${name} can be opened from other devices, but Bevro can't tell which address this device should use. It answers at ${address} on the computer it runs on.`;
}
