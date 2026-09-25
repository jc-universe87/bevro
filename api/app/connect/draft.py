"""ProviderDraft: what discovery found, before anything is saved as a provider.

The full draft is server-side. `public()` is the only view the browser gets:
no adapter block, no absolute paths, no secrets. The person confirms the
public view; Bevro turns the full draft into a provider.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from adapters.runtime import WORKER, RuntimeProfile, credentials_label

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
    # Other words this service uses for the same ability, taken from its own
    # addresses: a tag may say "shortlist" where every URL says
    # "opportunities", and the person may ask for either.
    terms: list[str] = Field(default_factory=list, max_length=6)
    # What this group does, split so a sentence can conjugate it:
    # "review" + "opportunities", with a second verb when one was earned.
    verb: str | None = Field(default=None, max_length=20)
    subject: str | None = Field(default=None, max_length=60)
    also: str | None = Field(default=None, max_length=20)
    action: str | None = Field(default=None, max_length=80)
    # How much of a reason this group is to have connected the thing at all.
    weight: float | None = None
    # Which version of the wording produced this.
    wording: int | None = None


class DraftAuth(BaseModel):
    required: bool = False
    # The stored-secret name (for HTTP: "api_key"; for commands: the environment variable).
    secret_name: str | None = None
    label: str | None = None
    hint: str | None = None


def _runs_at(rt: RuntimeProfile | None) -> str | None:
    """Where this will be driven from, said only when it is worth saying.

    Bevro reaching a service itself is the ordinary case and needs no
    explanation. A service only the host can see is worth saying out loud,
    because it means the worker has to be running for it to work at all.
    """
    if rt is None:
        return None
    return "On this machine" if rt.reachability.host_only() or rt.reachability.usable_from() == [WORKER] else None


class ProviderDraft(BaseModel):
    name: str = Field(max_length=120)
    # What a person reads. `settled()` makes sure this is that, and not the
    # service's own integration prose.
    description: str = Field(default="", max_length=2000)
    # What the thing said about itself, kept whole. Never shown as the
    # description; available under Advanced details and to reconnect.
    source_description: str | None = Field(default=None, max_length=4000)
    # The exact name the thing gave, before the part describing its own
    # plumbing was trimmed off ("Inventory REST API" -> "Inventory").
    source_name: str | None = Field(default=None, max_length=200)
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
    # Where this came from, in enough detail to find it again: the address
    # that was typed, the origin, the path under it, where the machine
    # description lives, and any scope that was resolved. Server-side only,
    # and kept on the provider so reconnect never has to guess.
    source: dict[str, Any] = Field(default_factory=dict)
    # When a service is scoped (a profile, a workspace) and has several, the
    # person chooses before connecting: [{"value": "eu-west", "label": "EU West"}]
    scope_choices: list[dict[str, str]] = Field(default_factory=list)

    @property
    def connected_for(self) -> str | None:
        """The one thing this connection is scoped to, in words. None if unscoped."""
        context = (self.adapter.get("config") or {}).get("context") or {}
        values = [str(v) for v in context.values() if v]
        return values[0] if len(values) == 1 else None

    @property
    def runtime(self) -> RuntimeProfile | None:
        for rt in self.runtimes:
            if rt.id == self.active_runtime:
                return rt
        return self.runtimes[0] if self.runtimes else None

    def with_runtimes(self, runtimes: list[RuntimeProfile], active_id: str | None = None, choice_needed: bool = False) -> "ProviderDraft":
        """Attach profiles and derive the draft's adapter, availability, wording and
        credential question from the active one. The single source of those fields."""
        from app.connect.runtimes import rank, select, settle_credentials

        # Whether anyone has to be asked for a credential is a question about
        # the project, not about one way into it, so it is settled here -
        # where every way in is known - and not by whichever found it first.
        self.runtimes = rank(settle_credentials(runtimes))
        # ...and then the choice is made *again*, because settling it may have
        # changed the answer: a way in that turned out to have what it needs
        # is a better one than the ranking knew about a moment ago. A caller's
        # `active_id` is a deliberate choice and is kept where it still can be
        # used; otherwise the ranking decides, in one place, once.
        chosen, _choice = select(self.runtimes)
        wanted = next((rt for rt in self.runtimes if rt.id == active_id and rt.invocable), None)
        self.active_runtime = wanted.id if wanted is not None else (chosen or (self.runtimes[0].id if self.runtimes else None))
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

    def settled(self) -> "ProviderDraft":
        """Sort out what a person reads from what the thing said about itself.

        Discovery collects whatever name and description it can find, and for
        a service that describes itself for integrators those are a page of
        implementation detail and a name ending in "control API". Both are
        kept, under their own fields, and what a person reads is *left blank*
        so that it is worked out fresh each time it is shown - copy generated
        once and stored goes stale the moment the generator improves.

        Doing this here means no discovery strategy has to remember to.
        """
        from app.connect import copy as provider_copy

        if self.source_name is None:
            self.source_name = self.name
            self.name = provider_copy.display_name(self.name)
        if self.source_description is None:
            found = (self.description or "").strip()
            if not provider_copy.is_fit_to_show(found):
                # Evidence, not copy. Kept whole; the card is built from facts.
                self.source_description = found[:4000] or ""
                self.description = ""
        return self

    def summary(self) -> str:
        """One sentence for the person, wherever the draft is shown."""
        from app.connect import copy as provider_copy

        return provider_copy.summary_for(
            self.name,
            [c.model_dump(exclude_none=True) for c in self.capabilities],
            stored=self.description,
        )

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
            "runs_at": _runs_at(rt),
            "runtime": rt.public() if rt else None,
            "runtime_options": [r.public() for r in self.runtimes if r.invocable] if self.choice_needed else [],
            "runtimes_found": len(self.runtimes),
            "choice_needed": self.choice_needed,
            "scope_choices": self.scope_choices,
            "connected_for": self.connected_for,
            "credentials_label": credentials_label(rt.credentials) if rt else None,
            "name": self.name,
            # The same sentence Agents will show once this is connected,
            # worked out the same way - so what is previewed is what is got.
            "description": self.summary(),
            "source_description": self.source_description,
            "source_name": self.source_name,
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
