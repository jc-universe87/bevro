import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import PageHeader, { Page } from "../components/PageHeader";
import { api, ApiError } from "../lib/api";

type Method = "api" | "mcp" | "command";

const METHODS: { value: Method; label: string }[] = [
  { value: "api", label: "API" },
  { value: "mcp", label: "MCP server" },
  { value: "command", label: "Command" },
];

/** The escape hatch: a handful of technical fields for what discovery cannot work out. */
export default function ConnectAdvanced() {
  const [method, setMethod] = useState<Method>("api");
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [workingDirectory, setWorkingDirectory] = useState("");
  const [secret, setSecret] = useState("");
  const [secretEnv, setSecretEnv] = useState("");
  const [template, setTemplate] = useState("");
  const [responseField, setResponseField] = useState("");
  const [inputFlag, setInputFlag] = useState("");
  const [tool, setTool] = useState("");
  const [capabilities, setCapabilities] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const details: Record<string, string> = {};
    const secrets: Record<string, string> = {};
    if (method === "api") {
      details.base_url = address.trim();
      if (template.trim()) details.request_template = template.trim();
      if (responseField.trim()) details.response_field = responseField.trim();
      if (secret.trim()) secrets.api_key = secret.trim();
    } else if (method === "mcp") {
      if (/^https?:\/\//i.test(address.trim())) details.server_url = address.trim();
      else details.command = address.trim();
      if (workingDirectory.trim()) details.working_directory = workingDirectory.trim();
      if (tool.trim()) details.tool = tool.trim();
      if (secret.trim()) secrets.api_key = secret.trim();
    } else {
      details.command = address.trim();
      if (workingDirectory.trim()) details.working_directory = workingDirectory.trim();
      if (inputFlag.trim()) details.input_flag = inputFlag.trim();
      if (secretEnv.trim() && secret.trim()) {
        details.secret_env = secretEnv.trim();
        secrets[secretEnv.trim()] = secret.trim();
      }
    }
    try {
      await api.connectProvider({
        name: name.trim(),
        description: "",
        capabilities: capabilities.split(",").map((c) => c.trim()).filter(Boolean),
        method,
        details,
        secrets,
        app_url: null,
      });
      navigate("/agents");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bevro couldn't reach the server.");
      setBusy(false);
    }
  };

  const addressLabel = method === "api" ? "Address" : method === "mcp" ? "Server address or command" : "Command";
  const addressPlaceholder = method === "api" ? "https://api.example.com" : method === "mcp" ? "http://localhost:3333/mcp  or  npx some-mcp-server" : "python -m my_agent";

  return (
    <Page narrow>
      <PageHeader title="Advanced setup">
        <Link to="/connect" className="bv-link text-sm">
          Back to Connect
        </Link>
      </PageHeader>
      <p className="bv-hint mb-5">For things Bevro couldn't work out on its own. The normal way is to type an address or folder under Connect.</p>
      <form onSubmit={submit} className="space-y-5" aria-label="Advanced setup">
        <fieldset>
          <legend className="bv-label">Connection type</legend>
          <div className="inline-flex rounded-md border border-line overflow-hidden" role="radiogroup" aria-label="Connection type">
            {METHODS.map((m) => (
              <label key={m.value} className={`px-4 py-2 text-sm cursor-pointer min-h-[40px] flex items-center ${method === m.value ? "bg-sunken font-medium" : "hover:bg-sunken"}`}>
                <input type="radio" name="method" value={m.value} checked={method === m.value} onChange={() => setMethod(m.value)} className="sr-only" />
                {m.label}
              </label>
            ))}
          </div>
        </fieldset>

        <div>
          <label htmlFor="a-name" className="bv-label">
            Name
          </label>
          <input id="a-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} className="bv-input" />
        </div>

        <div>
          <label htmlFor="a-address" className="bv-label">
            {addressLabel}
          </label>
          <input id="a-address" value={address} onChange={(e) => setAddress(e.target.value)} placeholder={addressPlaceholder} required className={`bv-input ${method === "api" ? "" : "font-mono text-sm"}`} />
        </div>

        {method !== "api" && (
          <div>
            <label htmlFor="a-cwd" className="bv-label">
              Working directory <span className="font-normal text-muted">(optional)</span>
            </label>
            <input id="a-cwd" value={workingDirectory} onChange={(e) => setWorkingDirectory(e.target.value)} placeholder="~/agents/my-agent" className="bv-input font-mono text-sm" />
            <p className="bv-hint mt-1">A folder on the machine that runs the worker, inside the approved local folders.</p>
          </div>
        )}

        {method === "command" && (
          <div>
            <label htmlFor="a-flag" className="bv-label">
              How the request is passed <span className="font-normal text-muted">(optional)</span>
            </label>
            <input id="a-flag" value={inputFlag} onChange={(e) => setInputFlag(e.target.value)} placeholder="--topic   (or: stdin)" className="bv-input font-mono text-sm" />
            <p className="bv-hint mt-1">An option name, "stdin", or leave empty to pass it as the last argument.</p>
          </div>
        )}

        {method === "mcp" && (
          <div>
            <label htmlFor="a-tool" className="bv-label">
              Tool to call <span className="font-normal text-muted">(optional)</span>
            </label>
            <input id="a-tool" value={tool} onChange={(e) => setTool(e.target.value)} placeholder="search:query" className="bv-input font-mono text-sm" />
            <p className="bv-hint mt-1">Tool name and the argument that takes the request. Discovery usually picks this itself.</p>
          </div>
        )}

        {method === "api" && (
          <>
            <div>
              <label htmlFor="a-template" className="bv-label">
                Request template <span className="font-normal text-muted">(optional)</span>
              </label>
              <input id="a-template" value={template} onChange={(e) => setTemplate(e.target.value)} placeholder='POST /ask {"query": "{request}"}' className="bv-input font-mono text-sm" />
              <p className="bv-hint mt-1">Leave empty for a service that speaks the Bevro contract (POST /invoke).</p>
            </div>
            <div>
              <label htmlFor="a-response" className="bv-label">
                Response field <span className="font-normal text-muted">(optional)</span>
              </label>
              <input id="a-response" value={responseField} onChange={(e) => setResponseField(e.target.value)} placeholder="answer" className="bv-input font-mono text-sm" />
            </div>
          </>
        )}

        <div>
          <label htmlFor="a-secret" className="bv-label">
            {method === "command" ? "Secret" : "API token"} <span className="font-normal text-muted">(optional)</span>
          </label>
          {method === "command" && (
            <input id="a-secret-env" aria-label="Environment variable name" value={secretEnv} onChange={(e) => setSecretEnv(e.target.value)} placeholder="OPENAI_API_KEY" className="bv-input font-mono text-sm mb-2" />
          )}
          <input id="a-secret" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} className="bv-input" />
          <p className="bv-hint mt-1">Stored encrypted on the server. It is never shown again.</p>
        </div>

        <div>
          <label htmlFor="a-caps" className="bv-label">
            Capabilities <span className="font-normal text-muted">(optional)</span>
          </label>
          <input id="a-caps" value={capabilities} onChange={(e) => setCapabilities(e.target.value)} placeholder="Research, Quotes, Bookings" className="bv-input" />
          <p className="bv-hint mt-1">A few words, separated by commas.</p>
        </div>

        <div className="flex items-center gap-3 pt-2">
          <button type="submit" className="bv-btn-primary" disabled={busy || !name.trim() || !address.trim()}>
            Connect
          </button>
          {error && (
            <p role="alert" className="text-sm">
              {error}
            </p>
          )}
        </div>
      </form>
    </Page>
  );
}
