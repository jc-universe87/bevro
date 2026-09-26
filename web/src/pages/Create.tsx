import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import Icon from "../components/Icon";
import Offered from "../components/Offered";
import PageHeader, { Page } from "../components/PageHeader";
import { api, ApiError, type CreatePreview, type CreateStatus } from "../lib/api";

const EXAMPLES = [
  "Research competitors in the note-taking app market and give me a short weekly report.",
  "Review pull requests and point out risky changes.",
  "Summarise new research papers about a topic I follow.",
];

export default function Create() {
  const [description, setDescription] = useState("");
  const [preview, setPreview] = useState<CreatePreview | null>(null);
  const [status, setStatus] = useState<CreateStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const navigate = useNavigate();

  useEffect(
    () => () => {
      if (pollRef.current !== null) window.clearTimeout(pollRef.current);
    },
    [],
  );

  const getPreview = async (e: FormEvent) => {
    e.preventDefault();
    if (description.trim().length < 3) return;
    setBusy(true);
    setError(null);
    try {
      setPreview(await api.createPreview(description.trim()));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  };

  /** Follow the build in plain steps. Nothing technical reaches this page. */
  const follow = (next: CreateStatus) => {
    setStatus(next);
    if (next.state === "ready" || next.state === "failed") {
      setBusy(false);
      return;
    }
    pollRef.current = window.setTimeout(async () => {
      try {
        follow(await api.createStatus(next.provider_id));
      } catch {
        setError("Bevro couldn't reach the server.");
        setBusy(false);
      }
    }, 2000);
  };

  const build = async () => {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      follow(await api.createBuild(preview, description.trim()));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
      setBusy(false);
    }
  };

  const retry = async () => {
    if (!status) return;
    setBusy(true);
    setError(null);
    try {
      follow(await api.createRebuild(status.provider_id, description.trim()));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
      setBusy(false);
    }
  };

  const building = status !== null && status.state !== "ready" && status.state !== "failed";

  return (
    <Page narrow>
      <PageHeader title="Create" lead={<>Describe something new, and Bevro has it built. To add something you already use, go to <Link to="/connect" className="bv-link">Connect</Link>.</>} />
      <form onSubmit={getPreview}>
        <label htmlFor="create-description" className="bv-label">
          What should this agent do?
        </label>
        <textarea
          id="create-description"
          value={description}
          onChange={(e) => {
            setDescription(e.target.value);
            setPreview(null);
            setStatus(null);
          }}
          rows={4}
          placeholder="Describe the work you want it to handle..."
          className="bv-input resize-y"
          disabled={building}
        />
        <div className="mt-3 flex items-center gap-3">
          <button type="submit" className="bv-btn-primary" disabled={busy || building || description.trim().length < 3}>
            Create
          </button>
          {error && (
            <p role="alert" className="text-sm">
              {error}
            </p>
          )}
        </div>
        {!preview && !status && (
          <ul className="mt-6 space-y-1.5 text-sm text-muted" aria-label="Examples">
            {EXAMPLES.map((example) => (
              <li key={example}>
                <button type="button" className="text-left hover:text-ink" onClick={() => setDescription(example)}>
                  “{example}”
                </button>
              </li>
            ))}
          </ul>
        )}
      </form>

      {preview && !status && (
        <section aria-label="Preview" className="mt-8 border-t bv-sep pt-6">
          <h2 className="text-xl font-semibold tracking-tight">{preview.name}</h2>
          <p className="mt-1 text-muted">{preview.description}</p>

          {preview.can.length > 0 && (
            <div className="mt-4">
              <p className="text-sm text-muted mb-1">Can:</p>
              <ul className="space-y-0.5" aria-label="Can">
                {preview.can.map((item) => (
                  <li key={item} className="text-sm">
                    • {item}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {preview.needs.length > 0 && (
            <div className="mt-4">
              <p className="text-sm text-muted mb-1">Needs:</p>
              <ul className="space-y-0.5" aria-label="Needs">
                {preview.needs.map((item) => (
                  <li key={item} className="text-sm">
                    • {item}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="mt-4 text-sm">
            <span className="text-muted">Produces:</span> {preview.produces}
            {preview.schedule && <span className="text-subtle"> · you asked for {preview.schedule}</span>}
          </p>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            {preview.can_build ? (
              <button type="button" className="bv-btn-primary" onClick={build} disabled={busy}>
                Create agent
              </button>
            ) : (
              <>
                <p className="text-sm basis-full">Creating agents needs a coding agent in your apps and agents.</p>
                <Link to="/connect" className="bv-btn-primary">
                  Connect coding agent
                </Link>
                <button type="button" className="bv-btn-quiet" onClick={() => setPreview(null)}>
                  Cancel
                </button>
              </>
            )}
          </div>
          {!preview.can_build && <Offered need={["agent_building", "coding"]} lead="Or add one Bevro already knows how to use:" />}
        </section>
      )}

      {status && (
        <section aria-label="Creating" className="mt-8 border-t bv-sep pt-6">
          {status.state === "ready" ? (
            <>
              <p className="flex items-center gap-2 font-medium">
                <Icon name="check" size={18} />
                Created
              </p>
              <h2 className="mt-3 text-xl font-semibold tracking-tight">{preview?.name}</h2>
              <p className="mt-1 text-muted">{preview?.description}</p>
              <div className="mt-4 flex flex-wrap gap-3">
                <button type="button" className="bv-btn-primary" onClick={() => navigate("/")}>
                  Ask
                </button>
                <Link to={`/apps/${status.provider_id}`} className="bv-btn">
                  View agent
                </Link>
              </div>
            </>
          ) : status.state === "failed" ? (
            <>
              <p role="alert" className="text-sm">
                {status.note}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button type="button" className="bv-btn" onClick={retry} disabled={busy}>
                  Retry
                </button>
                <Link to={`/apps/${status.provider_id}`} className="bv-link text-sm">
                  Review details
                </Link>
              </div>
            </>
          ) : (
            <>
              <p className="flex items-center gap-2 text-sm text-muted" aria-live="polite">
                <span className="bv-pulse inline-block h-2 w-2 rounded-full bg-accent" aria-hidden="true" />
                Preparing agent… {status.note}
              </p>
              <ol className="mt-2 space-y-0.5 text-sm">
                {status.steps.map((step, i) => (
                  <li key={step} className={i === status.steps.length - 1 ? "text-ink" : "text-muted"}>
                    {step}
                  </li>
                ))}
              </ol>
              <p className="bv-hint mt-2">This takes a few minutes. You can leave this page; it carries on.</p>
            </>
          )}
        </section>
      )}
    </Page>
  );
}
