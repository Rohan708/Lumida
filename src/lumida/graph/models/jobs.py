"""Jobs (Stage 4.5): work a person asks for in the web app or API ("run now", "test this
connection", "propose rules"), queued here and done by `lumida serve` within seconds.

At most one queued-or-running job of a kind per source (DB-enforced), so pressing "Run
now" twice runs once. A connection test for a database not added yet has no source; its
details are in ``payload`` (names only: the URL itself stays in `.env`).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from lumida.graph.models.base import Base, StrEnumType, TenantScoped, utcnow
from lumida.graph.models.enums import ACTIVE_JOB_STATUSES, JobKind, JobStatus

_ACTIVE = ", ".join(f"'{status.value}'" for status in ACTIVE_JOB_STATUSES)


class Job(TenantScoped, Base):
    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    data_source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("data_source.id"))
    kind: Mapped[JobKind] = mapped_column(StrEnumType(JobKind))
    status: Mapped[JobStatus] = mapped_column(StrEnumType(JobStatus), default=JobStatus.QUEUED)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    requested_by: Mapped[str] = mapped_column(String(320))
    requested_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)  # summary lines, counts
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index(
            "ux_job_active_per_source",
            "data_source_id",
            "kind",
            unique=True,
            postgresql_where=text(f"status IN ({_ACTIVE}) AND data_source_id IS NOT NULL"),
        ),
        Index("ix_job_status_requested", "status", "requested_at"),
        Index("ix_job_source_requested", "data_source_id", "requested_at"),
    )
