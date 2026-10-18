"""Jobs: "run now", connection tests and AI work asked for in the web app (Stage 4.5).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "job",
        sa.Column("id", UUID, nullable=False),
        sa.Column("data_source_id", UUID, nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("requested_by", sa.String(320), nullable=False),
        sa.Column("requested_at", TS, nullable=False),
        sa.Column("started_at", TS, nullable=True),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("result", postgresql.JSONB, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job")),
        sa.ForeignKeyConstraint(
            ["data_source_id"], ["data_source.id"], name=op.f("fk_job_data_source_id_data_source")
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name=op.f("fk_job_tenant_id_tenant")),
    )
    op.create_index(
        "ux_job_active_per_source",
        "job",
        ["data_source_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running') AND data_source_id IS NOT NULL"),
    )
    op.create_index("ix_job_status_requested", "job", ["status", "requested_at"])
    op.create_index("ix_job_source_requested", "job", ["data_source_id", "requested_at"])


def downgrade() -> None:
    op.drop_index("ix_job_source_requested", table_name="job")
    op.drop_index("ix_job_status_requested", table_name="job")
    op.drop_index("ux_job_active_per_source", table_name="job")
    op.drop_table("job")
