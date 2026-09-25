import type { BridgeStatus, ConnectDraft, DraftView } from "./api";

/**
 * Where a Connect attempt has got to, as the person sees it.
 *
 * Every state answers three questions: what did Bevro find, is anything in
 * the way, and what is the one thing to do next. The last one is `primary`,
 * and a state the person has to act on always has one - `stepProblems`
 * checks that, and the tests run it over every state.
 *
 * Nothing here decides anything about the provider. The server says what is
 * true (is it invocable, does it need a credential, what went wrong); this
 * only chooses the words and the button for it.
 */
export type StepKind =
  | "resolving"
  | "discovering"
  | "choice_required"
  | "trust_required"
  | "testing"
  | "found_ready"
  | "needs_description"
  | "needs_credential"
  | "needs_interface"
  | "needs_start"
  | "needs_choice"
  | "building"
  | "already_connected"
  | "connected"
  | "worker_unavailable"
  | "not_found"
  | "unreachable"
  | "failed";

export type ActionId =
  | "connect"
  | "describe"
  | "add_credential"
  | "connect_without_credential"
  | "setup"
  | "build"
  | "allow"
  | "choose"
  | "retry"
  | "check_again"
  | "test"
  | "open"
  | "try_another";

export interface StepAction {
  id: ActionId;
  label: string;
}

export interface Step {
  kind: StepKind;
  /** What Bevro found, or what is happening. */
  heading: string;
  /** What is in the way, or what happens next. */
  message?: string;
  /** The one thing to do next. */
  primary?: StepAction;
  /** Genuinely optional: Test, Try again, a different route. */
  secondary?: StepAction;
  /** Quiet ways out: Advanced setup, Cancel, a different choice. */
  tertiary?: StepAction[];
  /** An optional "Why?" and its answer. The page makes sense without it. */
  help?: { question: string; answer: string };
  /** The person has to type or pick something here. */
  input?: "description" | "credential" | "choice" | "list";
  /** Bevro is working; nothing is asked of the person. */
  busy?: boolean;
}

export interface LocalState {
  /** A credential has been typed and kept for Connect. */
  credentialAdded?: boolean;
  /** Building a connection for something that had none. */
  bridge?: BridgeStatus | null;
}

const WORKER_HELP =
  "Bevro looks at things on this computer through a small helper program called the worker, and it isn't answering. " +
  "Start it with ./scripts/worker.sh (see the setup guide), then try again.";

function found(draft: DraftView, local: LocalState): Step {
  const name = draft.name || "This";
  const canBuild = Boolean(draft.needs_bridge && draft.bridge_possible);

  // Running, with a website and nothing another program can use. Saying what
  // it is for would not change that, so this comes before asking.
  if (!draft.invocable && draft.web_ui && draft.availability !== "needs_start") {
    return {
      kind: "needs_interface",
      heading: draft.mechanism === "local" ? `${name} is running on this machine.` : `${name} is running.`,
      message: "It only has its own website so far: nothing another program can send work to.",
      primary: canBuild ? { id: "build", label: "Set up how to use it" } : { id: "setup", label: "Set up how to use it" },
      tertiary: canBuild ? [{ id: "setup", label: "Set it up by hand" }] : undefined,
      help: {
        question: "Why can't Bevro use it yet?",
        answer:
          `Bevro can see that ${name} is running, but hasn't found a safe way for another program to send it work - its website is made for people. ` +
          (canBuild
            ? `Setting it up has one of your coding agents build a small connection for it, kept inside Bevro. ${name} itself isn't changed.`
            : `If ${name} does have one that Bevro didn't recognise, the next page lets you describe it.`),
      },
    };
  }

  if (draft.needs_description) {
    return {
      kind: "needs_description",
      heading: "What should Bevro use it for?",
      input: "description",
      primary: { id: "describe", label: "Continue" },
      help: {
        question: "Why am I being asked?",
        answer: `Bevro couldn't tell what ${name} does from what it could see. A few words let Bevro know when to send work here. You can change them later.`,
      },
    };
  }

  if (!draft.invocable) {
    if (draft.availability === "needs_start") {
      return {
        kind: "needs_start",
        heading: `${name} isn't running.`,
        message: "Start it on this machine, then check again.",
        primary: { id: "check_again", label: "Check again" },
        tertiary: [{ id: "setup", label: "Advanced setup" }],
        help: {
          question: "Why does it need to be running?",
          answer: `${name} is a service that answers while it's running. Bevro reads how to use it once it's up; nothing about it is changed.`,
        },
      };
    }
    return {
      kind: "needs_interface",
      heading: `${name} is almost ready.`,
      message: "Bevro found it, but not yet a way to send it a task.",
      primary: canBuild ? { id: "build", label: "Set up how to use it" } : { id: "setup", label: "Set up how to use it" },
      tertiary: canBuild ? [{ id: "setup", label: "Set it up by hand" }] : undefined,
      help: {
        question: "Why can't Bevro use it yet?",
        answer: canBuild
          ? `Bevro can see ${name}, but nothing in it can be handed a task as it is. Setting it up has one of your coding agents build a small connection for it, kept inside Bevro. ${name} itself isn't changed.`
          : `Bevro can see ${name}, but couldn't find a way for other programs to send it work - it may only have a screen for people. If it does have one that Bevro didn't recognise, the next page lets you describe it.` +
            (draft.needs_bridge ? " A coding agent connected to Bevro could also build one." : ""),
      },
    };
  }

  const scoped = (draft.scope_choices ?? []).length > 0;
  const twoWays = draft.choice_needed && draft.runtime_options.length > 1;
  if (scoped || twoWays) {
    return {
      kind: "needs_choice",
      heading: scoped ? `Bevro found ${draft.scope_choices!.length}. Which should this connection use?` : "Bevro found two ways to connect this. Which should it use?",
      input: "choice",
      primary: { id: "connect", label: "Connect" },
      help: scoped
        ? { question: "Why choose?", answer: `${name} holds more than one of these. A connection works with one, so Bevro asks rather than guessing.` }
        : { question: "What's the difference?", answer: "Both work. They differ in how Bevro reaches it and what they need; Bevro keeps the other as a fallback." },
    };
  }

  if (draft.auth.required && !local.credentialAdded) {
    const label = draft.auth.label ?? "credential";
    return {
      kind: "needs_credential",
      heading: `${name} needs ${/^[aeiou]/i.test(label) ? "an" : "a"} ${label}.`,
      message: draft.auth.hint ?? undefined,
      input: "credential",
      primary: { id: "add_credential", label: "Add credential" },
      // What else is in place can be checked before the credential is added.
      secondary: { id: "test", label: "Test" },
      tertiary: [{ id: "connect_without_credential", label: "Connect now, add it later" }],
      help: draft.auth.why
        ? { question: "Why can't Bevro use the existing one?", answer: draft.auth.why }
        : { question: "What is this credential for?", answer: `${name} needs it to do its work. Bevro gives it to ${name} only when it runs a task. It's stored encrypted and never shown again.` },
    };
  }

  return {
    kind: "found_ready",
    heading: "Ready to connect.",
    message: local.credentialAdded ? `${draft.auth.label ?? "Credential"} added. It's stored encrypted when you connect.` : undefined,
    primary: { id: "connect", label: "Connect" },
    secondary: { id: "test", label: "Test" },
    help: {
      question: "What does Connect do?",
      answer: `It adds ${name} to your agents, so Bevro can send it suitable work. Nothing about ${name} itself is changed, and you can pause or remove it any time.`,
    },
  };
}

export function connectStep(d: ConnectDraft | null, local: LocalState = {}): Step | null {
  if (!d) return null;

  if (local.bridge) {
    const b = local.bridge;
    if (b.state === "ready") return { kind: "connected", heading: "Connected", message: b.note, primary: { id: "open", label: "Go to Agents" } };
    if (b.state === "failed") return { kind: "failed", heading: b.note, primary: { id: "build", label: "Try again" }, tertiary: [{ id: "setup", label: "Set it up by hand" }] };
    return { kind: "building", heading: b.note, message: "This usually takes a few minutes. You can leave this page; it carries on.", busy: true };
  }

  switch (d.state) {
    case "looking":
      return { kind: d.target_kind === "name" ? "resolving" : "discovering", heading: `Looking for ${d.target_label}…`, busy: true };
    case "choice_required":
      return {
        kind: "choice_required",
        heading: "Found a few matches on this machine.",
        message: "Which one did you mean?",
        input: "list",
        primary: { id: "choose", label: "Choose" },
        help: { question: "Why am I being asked?", answer: "More than one folder here has that name. Bevro looks only inside the one you choose." },
      };
    case "trust_required": {
      const command = d.trust?.kind === "command";
      return {
        kind: "trust_required",
        heading: command ? "This runs software on this machine." : "This is a project on this machine.",
        message: command ? `Allow Bevro to use ${d.trust?.label}?` : "Allow Bevro to look inside this folder and work in it?",
        primary: { id: "allow", label: command ? "Allow program" : "Allow folder" },
        help: {
          question: command ? "Why do I need to allow this program?" : "Why do I need to allow this folder?",
          answer: `Bevro never looks at or runs anything on this computer until you say so. This allows only this ${command ? "program" : "folder"}, and you can take it back any time under Settings.`,
        },
      };
    }
    case "testing":
      return { kind: "testing", heading: "Testing…", busy: true };
    case "connected":
      return { kind: "connected", heading: "Connected", primary: { id: "open", label: "Go to Agents" } };
    case "failed": {
      const error = d.error ?? "Bevro couldn't connect that.";
      if (d.problem === "worker") {
        return {
          kind: "worker_unavailable",
          heading: "Bevro can't reach this machine right now.",
          primary: { id: "retry", label: "Try again" },
          help: { question: "Check the worker", answer: WORKER_HELP },
        };
      }
      if (d.problem === "not_found") {
        return {
          kind: "not_found",
          heading: error,
          primary: { id: "try_another", label: "Try another name" },
          help: {
            question: "Where does Bevro look?",
            answer: "In folders you've already allowed, next to things you've connected, and in your home folder - by name only. It doesn't look inside anything until you allow it.",
          },
        };
      }
      return {
        kind: d.problem === "unreachable" ? "unreachable" : "failed",
        heading: error,
        primary: { id: "retry", label: "Try again" },
        tertiary: [{ id: "setup", label: "Set it up by hand" }],
      };
    }
    case "found": {
      if (!d.draft) return { kind: "failed", heading: "Bevro lost track of what it found.", primary: { id: "retry", label: "Try again" } };
      if (d.already_connected) {
        return {
          kind: "already_connected",
          heading: `${d.already_connected.name} is already connected.`,
          primary: { id: "open", label: "Open in Agents" },
          help: { question: "Can I connect it again?", answer: "There's no need: it's already one of your agents. Manage it there to test it, add a credential, or look at it again." },
        };
      }
      // A failed Test must not leave the old, misleading Connect button in
      // place. Credential failures already have a purpose-built state below;
      // other failures lead to setup, with another Test as the quiet retry.
      if (d.test && !d.test.ok && d.test.next !== "add_credential" && d.draft.invocable && d.draft.availability !== "needs_start") {
        return {
          kind: "failed",
          heading: `${d.draft.name || "This"} needs attention.`,
          message: d.test.detail ?? "Bevro found it, but one of the checks did not pass.",
          primary: { id: "setup", label: "Set up how to use it" },
          secondary: { id: "test", label: "Try again" },
          help: {
            question: "What did Bevro check?",
            answer: "The results below list each check separately. No real task is run unless the provider advertises a harmless self-check of its own.",
          },
        };
      }
      return found(d.draft, local);
    }
  }
  return null;
}

/**
 * What is wrong with a step, as the page would show it. Empty when nothing.
 * The rule: a step either says Bevro is working, or offers one thing to do.
 */
export function stepProblems(step: Step): string[] {
  const problems: string[] = [];
  if (step.busy && step.primary) problems.push(`${step.kind}: working, yet asks for something`);
  if (!step.busy && !step.primary) problems.push(`${step.kind}: nothing to do next`);
  if (step.input && !step.primary) problems.push(`${step.kind}: asks for input with no way to confirm it`);
  if (step.primary && step.secondary && step.primary.label === step.secondary.label) problems.push(`${step.kind}: two buttons say the same thing`);
  if (/runtime|systemd|compose|stdio|openapi|environmentfile/i.test(`${step.heading} ${step.message ?? ""} ${step.primary?.label ?? ""}`)) {
    problems.push(`${step.kind}: technical words in the normal path`);
  }
  return problems;
}
