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
  if (!surface) return null;
  const candidates = surface.candidates?.length ? surface.candidates : surface.url ? [{ url: surface.url, reach: surface.reach ?? "loopback" }, ...(surface.local_url ? [{ url: surface.local_url, reach: "loopback" }] : [])] : [];
  let best: { href: string; tier: number } | null = null;
  let elsewhere: Opening | null = null;
  candidates.forEach((c) => {
    const judged = judge(c, at);
    if (judged === null) return;
    if ("reason" in judged) {
      elsewhere ??= judged;
      return;
    }
    // Lower tier wins; among equals, the order the server gave (most suitable in general first).
    if (best === null || judged.tier < best.tier) best = judged;
  });
  if (best !== null) return { kind: "link", href: (best as { href: string }).href };
  return elsewhere;
}

/**
 * One candidate address, for this browser: a link and how good a choice it
 * is (lower is better), where it opens instead, or nothing (not an address).
 *
 *   0  an address the person gave Bevro
 *   1  on the very host this browser used to reach Bevro - which it has
 *      just shown it can resolve and reach
 *   2  this machine's own address, for a browser on this machine
 *   3  a network address of the same sort as the one this browser used
 *      (an IP address for an IP address, a name for a name)
 *   4  any other network or shared address
 *
 * An address that only answers on the machine it runs on is never offered
 * to a browser anywhere else.
 */
function judge(c: { url: string; reach: string }, at: Here): { href: string; tier: number } | Extract<Opening, { kind: "elsewhere" }> | null {
  let url: URL;
  try {
    url = new URL(c.url);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  const here = isLoopbackHost(at.hostname);
  const local = isLoopbackHost(url.hostname);
  if (c.reach === "all_interfaces" && local && !here) {
    // Published on every interface of Bevro's machine: the name this browser
    // used for that machine reaches it too - when that name is the machine's.
    if (!seesTheMachineDirectly(at)) return { kind: "elsewhere", address: url.toString(), reason: "unknown_host" };
    url.hostname = at.hostname;
  } else if (local && !here) {
    return { kind: "elsewhere", address: url.toString(), reason: "this_computer" };
  }
  const href = url.toString();
  if (c.reach === "explicit") return { href, tier: 0 };
  if (sameHost(url.hostname, at.hostname)) return { href, tier: 1 };
  if (here && local) return { href, tier: 2 };
  if (IP_LITERAL.test(url.hostname) === IP_LITERAL.test(at.hostname)) return { href, tier: 3 };
  return { href, tier: 4 };
}

function sameHost(a: string, b: string): boolean {
  return a.replace(/^\[|\]$/g, "").toLowerCase() === b.replace(/^\[|\]$/g, "").toLowerCase();
}

export type HubActionId = "use" | "open" | "add_credential" | "choose" | "retry" | "setup_direct" | "how_to" | "how_to_open" | "resume";

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

/** "an OpenAI API key", or "a credential" when Bevro can't tell which. */
export function credentialWords(needs: string | undefined): string {
  if (!needs) return "a credential";
  return `${/^[aeiou]/i.test(needs) ? "an" : "a"} ${needs}`;
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
    directNote: !p.enabled
      ? null
      : state === "needs_credential"
        ? `Direct use in Bevro needs ${credentialWords(p.direct?.needs)}`
        : state === "needs_choice"
          ? "Direct use in Bevro needs you to say which one it's for"
          : null,
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
    state === "needs_choice"
      ? { id: "choose", label: "Choose which one" }
      : state === "needs_credential"
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
