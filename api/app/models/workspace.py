"""An approved directory a coding provider may work in.

Only rows in this table can ever be used; they come from server-side
configuration, never from the browser. The path is never sent to the browser.
"""

from typing import Any

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import Timestamped, UUIDPrimaryKey


class Workspace(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "workspaces"

    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # e.g. ["read", "write", "run_commands"] - the most a run here may be granted.
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=lambda: ["read"])
    # Directories a run here may *read* but never write: the project a bridge
    # is being built against, for instance. Absolute paths, server-side only.
    read_paths: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # True for a workspace Bevro made for its own purposes (a bridge folder).
    # Kept out of the person's project list.
    internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Optional repository metadata, e.g. {"remote_url": "https://github.com/..."}.
    repository: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
