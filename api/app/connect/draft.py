"""ProviderDraft: what discovery found, before anything is saved as a provider.

The full draft is server-side. `public()` is the only view the browser gets:
no adapter block, no absolute paths, no secrets. The person confirms the
public view; Bevro turns the full draft into a provider.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from adapters.runtime import RuntimeProfile, credentials_label

Confidence = Literal["high", "medium", "low"]
Availability = Literal["ready", "needs_worker", "needs_start", "not_invocable"]

CONFIDENCE_LABELS = {"high": "Confident", "medium": "Likely", "low": "Needs review"}
MECHANISM_LABELS = {
    "http": "API",
    "mcp": "MCP server",
    "command": "Local agent",
    "local": "Local project",
}
LOW_CONFIDENCE_NOTE = "I found this provider but I'm not fully sure what it can do."


class DraftCapability(BaseModel):
    id: str = Field(max_length=80)
    title: str = Field(max_length=80)
    description: str | None = Field(default=None, max_length=200)


class DraftAuth(BaseModel):
    required: bool = False
    # The stored-secret name (for HTTP: "api_key"; for commands: the environment variable).
    secret_name: str | None = None
    label: str | None = None
    hint: str | None = None


class ProviderDraft(BaseModel):
    name: str = Field(max_length=120)
    description: str = Field(default="", max_length=2000)
    capabilities: list[DraftCapability] = Field(default_factory=list)
    # "http" | "mcp" | "command" | "local" (found, but nothing to run yet)
    mechanism: str
    mechanism_label: str = ""
    # The full adapter block a Provider would get. Server-side only.
    adapter: dict[str, Any] = Field(default_factory=dict)
    # How the person will see the way Bevro talks to it: no absolute paths.
    invocation_label: str | None = None
    availability: Availability = "ready"
    confidence: Confidence = "medium"
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    app_url: str | None = None
    auth: DraftAuth = Field(default_factory=DraftAuth)
    invocable: bool = True
    # Where the LLM assist may have refined things; kept for the record.
    assisted: bool = False
    # Small, named, bounded extras the optional assist may see (README excerpt,
    # dependency names, tool descriptions). Server-side only; never .env, never paths.
    assist_evidence: dict[str, Any] = Field(default_factory=dict)
    # Public functions, classes or exports found by reading the project. When a
    # project has these but nothing that can take a task, Bevro can offer to
    # build a small connection of its own. Server-side only.
    callable_evidence: dict[str, Any] = Field(default_factory=dict)
    # How this installation can run or be reached: every profile found, and the one chosen.
    runtimes: list[RuntimeProfile] = Field(default_factory=list)
    active_runtime: str | None = None
    # Two different mechanisms were close: the person picks under "I found two ways to connect this."
    choice_needed: bool = False

    @property
    def runtime(self) -> RuntimeProfile | None:
        for rt in self.runtimes:
            if rt.id == self.active_runtime:
                return rt
        return self.runtimes[0] if self.runtimes else None

    def with_runtimes(self, runtimes: list[RuntimeProfile], active_id: str | None = None, choice_needed: bool = False) -> "ProviderDraft":
        """Attach profiles and derive the draft's adapter, availability, wording and
        credential question from the active one. The single source of those fields."""
        from app.connect.runtimes import rank

        self.runtimes = rank(runtimes)
        self.active_runtime = active_id or (self.runtimes[0].id if self.runtimes else None)
        self.choice_needed = choice_needed
        rt = self.runtime
        if rt is None:
            return self
        self.adapter = dict(rt.adapter)
        self.mechanism = {"http": "http", "mcp": "mcp", "command": "command", "local": "local"}.get(rt.adapter_kind, rt.adapter_kind or "local")
        self.mechanism_label = rt.display_name
        self.availability = rt.availability  # type: ignore[assignment]
        self.invocable = rt.invocable
        creds = rt.credentials
        self.auth = DraftAuth(required=creds.required_from_user, secret_name=creds.names[0] if creds.names else None, label=_secret_label(creds.names[0]) if creds.names else None, hint=creds.note)
        return self

    @property
    def needs_bridge(self) -> bool:
        """Worth connecting, but nothing in it can take a task as it stands."""
        evidence = self.callable_evidence or {}
        return not any(rt.invocable for rt in self.runtimes) and bool(evidence.get("modules") or evidence.get("exports"))

    def public(self) -> dict[str, Any]:
        rt = self.runtime
        return {
            "needs_bridge": self.needs_bridge,
            "runs_via": rt.display_name if rt else None,
            "runtime": rt.public() if rt else None,
            "runtime_options": [r.public() for r in self.runtimes if r.invocable] if self.choice_needed else [],
            "runtimes_found": len(self.runtimes),
            "choice_needed": self.choice_needed,
            "credentials_label": credentials_label(rt.credentials) if rt else None,
            "name": self.name,
            "description": self.description,
            "capabilities": [c.model_dump() for c in self.capabilities],
            "mechanism": self.mechanism,
            "mechanism_label": self.mechanism_label or MECHANISM_LABELS.get(self.mechanism, self.mechanism),
            "invocation_label": self.invocation_label,
            "availability": self.availability,
            "confidence": self.confidence,
            "confidence_label": CONFIDENCE_LABELS[self.confidence],
            "note": LOW_CONFIDENCE_NOTE if self.confidence == "low" else None,
            "evidence": list(self.evidence),
            "warnings": list(self.warnings),
            "app_url": self.app_url,
            "auth": self.auth.model_dump(),
            "invocable": self.invocable,
        }


_SECRET_LABELS = {
    "OPENAI_API_KEY": "OpenAI credential",
    "ANTHROPIC_API_KEY": "Anthropic credential",
    "GOOGLE_API_KEY": "Google credential",
    "MISTRAL_API_KEY": "Mistral credential",
    "COHERE_API_KEY": "Cohere credential",
    "GROQ_API_KEY": "Groq credential",
    "api_key": "API token",
}


def _secret_label(name: str) -> str:
    return _SECRET_LABELS.get(name) or name.replace("_", " ").title()


def not_found(name: str, mechanism: str, message: str, evidence: list[str] | None = None) -> ProviderDraft:
    """A draft for something Bevro could see but cannot use yet."""
    return ProviderDraft(
        name=name,
        mechanism=mechanism,
        adapter={},
        availability="not_invocable",
        confidence="low",
        evidence=evidence or [],
        warnings=[message],
        invocable=False,
    )
