import type { TestResult } from "../lib/api";

/** What Bevro verified, one fact per line and never by colour alone. */
export default function TestResults({ result }: { result: TestResult }) {
  return (
    <section className="mt-4" aria-label="Test results" aria-live="polite">
      <h3 className="text-sm font-medium">{result.ok ? "Test passed." : "Test found a problem."}</h3>
      {(result.checks ?? []).length > 0 && (
        <ul className="mt-1.5 space-y-1 text-sm" aria-label="What was checked">
          {(result.checks ?? []).map((check, index) => (
            <li key={`${check.label}-${index}`} className="flex items-start gap-2">
              <span aria-hidden="true" className={`w-4 shrink-0 text-center ${check.ok === false ? "text-ink font-semibold" : "text-muted"}`}>
                {check.ok === true ? "✓" : check.ok === false ? "✗" : "–"}
              </span>
              <span>
                <span className="sr-only">{check.ok === true ? "Passed: " : check.ok === false ? "Missing: " : "Not checked: "}</span>
                {check.label}
                {check.detail && <span className="block text-muted">{check.detail}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {result.detail && <p className="mt-1.5 text-sm text-muted">{result.detail}</p>}
    </section>
  );
}
