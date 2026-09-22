"""A generic artifact: any result a task produced.

One table for every kind of output. `type` is a free string so that a
provider can hand back something Bevro has never heard of; the renderer then
falls back to "Open result".
"""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._common import Timestamped, UUIDPrimaryKey


class Artifact(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "artifacts"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_runs.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payload: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # "metadata" is reserved on SQLAlchemy declarative classes; the column keeps the name.
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    task: Mapped["Task"] = relationship(back_populates="artifacts")  # noqa: F821
    provider_run: Mapped["ProviderRun | None"] = relationship(back_populates="artifacts")  # noqa: F821
