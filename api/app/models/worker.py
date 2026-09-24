"""A WorkerHeartbeat is one Bevro worker saying "I am here, and this is where".

Bevro used to infer a worker from recent availability reports on background
providers. That works only once such a provider exists: a fresh installation
whose first connection is a web service the container cannot reach had no way
to know a worker was there to try from. A worker now says so itself.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import utcnow


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    # "hostname:pid" - one row per running worker, replaced as it reports.
    worker_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    # Adapter kinds this worker will pick work up for.
    kinds: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Where it sits: "host" for a worker running on the machine itself, which
    # is the whole reason it can reach services the API container cannot.
    network: Mapped[str] = mapped_column(String(20), nullable=False, default="host")
    # Room for what a worker may later want to say about itself.
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
