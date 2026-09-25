import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../lib/api";

type Offer = { slug: string; name: string; description: string; capabilities: string[] };

/**
 * Things Bevro knows how to use that the person hasn't added. Nothing here is
 * in their Apps & agents until they choose it; removed, it is simply offered
 * again. `need` narrows the offer to what can do one kind of work.
 */
export default function Offered({ need, lead }: { need?: string[]; lead: string }) {
  const [offers, setOffers] = useState<Offer[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  useEffect(() => {
    api
      .offeredIntegrations()
      .then((list) => setOffers(need ? list.filter((o) => o.capabilities.some((c) => need.includes(c))) : list))
      .catch(() => setOffers([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (!offers.length) return null;
  const add = async (o: Offer) => {
    setBusy(o.slug);
    setError(null);
    try {
      const added = await api.addIntegration(o.slug);
      navigate(`/apps/${added.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server. Try again in a moment.");
      setBusy(null);
    }
  };
  return (
    <section aria-label="Also available" className="mt-10 border-t bv-sep pt-5">
      <p className="bv-meta">{lead}</p>
      <ul className="mt-2 space-y-2">
        {offers.map((o) => (
          <li key={o.slug} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <span>
              <span className="font-medium">{o.name}</span>
              {o.description && <span className="text-muted"> · {o.description}</span>}
            </span>
            <button type="button" className="bv-btn" onClick={() => void add(o)} disabled={busy !== null}>
              {busy === o.slug ? "Adding…" : `Add ${o.name}`}
            </button>
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="mt-2 text-sm">
          {error}
        </p>
      )}
    </section>
  );
}
