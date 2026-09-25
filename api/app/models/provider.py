"""A Provider is anything capable of doing work.

Nothing here says what *kind* of thing it is. An AI agent, a workflow, a SaaS
application, a person or something not invented yet are all just providers
with different capability declarations and a different adapter reference.
"""

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._common import Timestamped, UUIDPrimaryKey


class Provider(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "providers"

    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # The exact name the thing gave, when Bevro trimmed the part that only
    # described its interface ("Inventory REST API" -> "Inventory").
    source_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # One short sentence a person reads on the card. Never a service's own
    # integration prose: that goes in `source_description` and is shown under
    # Advanced details. app/connect/copy.py is where the difference lives.
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # What the thing said about itself when Bevro found it: an OpenAPI
    # `info.description`, a README paragraph, a manifest line. Evidence, kept
    # whole so that reconnecting and troubleshooting have something to read.
    source_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Machine-readable capability declarations, e.g.
    # [{"id": "research", "title": "Research", "description": "..."}]
    capabilities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # The active runtime's adapter block, denormalised for queries and older code:
    # {"kind": "local"|"http"|"mcp"|"command"|..., "ref": "...", "config": {...}}.
    # Never holds secrets - those live in ProviderSecret. Kept in step with `runtimes`.
    adapter: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # How this installation runs or is reached: a list of RuntimeProfile dicts
    # (adapters/runtime.py), one of which is active. Server-side only.
    runtimes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    active_runtime: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # What Connect was given ({"kind": "local"|"url"|..., "target": "..."}), so it can look again. Server-side only.
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # An address for the person's browser: one they gave, or one the provider
    # declares for itself. A website Bevro merely found is a surface instead.
    app_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # How the person uses it apart from Bevro, as evidence: its own web app, a
    # chat bot, a schedule, a command line (app/connect/surfaces.py). Refreshed
    # whenever discovery looks again; turned into words when shown.
    surfaces: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # e.g. {"kind": "letter", "text": "M"} - metadata only, never raw image bytes.
    icon: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    # Which version of discovery gathered this provider's evidence. Below the
    # current one, the worker looks again - once - rather than leaving the
    # provider saying what an older Bevro concluded.
    discovery_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Where the record came from: "example", "created", "connected".
    origin: Mapped[str] = mapped_column(String(40), nullable=False, default="connected")
    # Last health report, written by the process that can reach the provider
    # (the worker, for background providers):
    # {"state": "available"|"not_installed"|"not_authenticated"|"unavailable", "checked_at": iso}
    availability: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    secrets: Mapped[list["ProviderSecret"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )
    runs: Mapped[list["ProviderRun"]] = relationship(back_populates="provider")  # noqa: F821


class ProviderSecret(UUIDPrimaryKey, Timestamped, Base):
    """A named secret belonging to a provider, encrypted at rest.

    The value is only ever decrypted inside the API when an adapter needs it.
    Nothing here is ever serialised back to the browser.
    """

    __tablename__ = "provider_secrets"
    __table_args__ = (UniqueConstraint("provider_id", "name", name="uq_provider_secret_name"),)

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    provider: Mapped[Provider] = relationship(back_populates="secrets")
