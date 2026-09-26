import { useEffect, useRef, useState, type RefObject } from "react";
import { api, ApiError, type Provider, type RemovalPlan } from "../lib/api";

/**
 * Taking something out of Apps & agents, asked the same way wherever it is
 * asked from: the item's own page, or the quiet menu on its row.
 */

/** What removal really does, in the order that matters: what goes, then what stays. */
export function removalConsequences(provider: Provider, plan: RemovalPlan | null): string[] {
  const lines: string[] = [];
  if ((plan?.in_flight ?? 0) > 0) {
    return [`${provider.name} is working on something right now. Wait for it to finish, or cancel it first.`];
  }
  if (plan?.built_project) lines.push("The project Bevro wrote for it is deleted.");
  if (plan?.built_connection) lines.push("The connection Bevro built for it is deleted.");
  if ((plan?.credentials ?? 0) > 0) {
    lines.push(plan?.credentials === 1 ? "Its stored credential is deleted." : `Its ${plan?.credentials} stored credentials are deleted.`);
  }
  lines.push(`Nothing outside Bevro is touched: ${provider.name} itself stays exactly as it is.`);
  if ((plan?.history ?? 0) > 0) {
    const n = plan?.history ?? 0;
    lines.push(`${n} ${n === 1 ? "task stays" : "tasks stay"} in Recent, still showing ${provider.name} as having done the work.`);
  }
  return lines;
}

export interface Removal {
  asking: boolean;
  plan: RemovalPlan | null;
  busy: boolean;
  note: string | null;
  ask: () => Promise<void>;
  cancel: () => void;
  confirm: () => Promise<void>;
}

/**
 * The facts are fetched before the question is shown, so it is never asked
 * without them. Backing out puts focus back where the person came from.
 */
export function useRemoval(id: string, onRemoved: () => void, returnFocus?: RefObject<HTMLElement>): Removal {
  const [asking, setAsking] = useState(false);
  const [plan, setPlan] = useState<RemovalPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const wasAsking = useRef(false);

  useEffect(() => {
    if (wasAsking.current && !asking) returnFocus?.current?.focus();
    wasAsking.current = asking;
  }, [asking, returnFocus]);

  return {
    asking,
    plan,
    busy,
    note,
    ask: async () => {
      setNote(null);
      setPlan(await api.removalPlan(id).catch(() => null));
      setAsking(true);
    },
    cancel: () => {
      setAsking(false);
      setPlan(null);
      setNote(null);
    },
    confirm: async () => {
      setBusy(true);
      setNote(null);
      try {
        await api.removeProvider(id);
        onRemoved();
      } catch (err) {
        setNote(err instanceof ApiError ? err.message : "Bevro couldn't reach the server. Try again in a moment.");
        setBusy(false);
      }
    },
  };
}

/** The question itself. Cancel is where focus lands: the safe choice comes first. */
export function RemoveQuestion({ provider, removal, className = "" }: { provider: Provider; removal: Removal; className?: string }) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => cancelRef.current?.focus(), []);
  const headingId = `remove-heading-${provider.id}`;
  return (
    <div role="group" aria-labelledby={headingId} className={`bv-panel ${className}`}>
      <p id={headingId} className="font-medium">
        Remove {provider.name} from Bevro?
      </p>
      <p className="mt-1 text-sm">
        This removes {provider.name} from your Apps & agents list and removes Bevro's current setup for it. Your previous task history will remain.
      </p>
      <ul className="mt-2 space-y-0.5 text-sm text-muted">
        {removalConsequences(provider, removal.plan).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {removal.note && (
        <p role="alert" className="mt-2 text-sm">
          {removal.note}
        </p>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <button ref={cancelRef} type="button" className="bv-btn-quiet" onClick={removal.cancel}>
          Cancel
        </button>
        <button type="button" className="bv-btn-danger" onClick={() => void removal.confirm()} disabled={removal.busy || (removal.plan?.in_flight ?? 0) > 0}>
          {removal.busy ? "Removing…" : "Remove from Bevro"}
        </button>
      </div>
    </div>
  );
}
