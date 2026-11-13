"""Persist the finding category used for conservative incident correlation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_incident_finding_category"
down_revision: str | None = "0004_incident_intelligence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("finding_category", sa.String(100), nullable=True))
    op.execute(
        "UPDATE incidents SET finding_category = 'legacy_uncategorized' "
        "WHERE finding_category IS NULL"
    )
    op.alter_column("incidents", "finding_category", nullable=False)
    op.create_index(
        "ix_incidents_correlation_candidates",
        "incidents",
        ["dataset_id", "finding_category", "status", "last_observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_incidents_correlation_candidates", table_name="incidents")
    op.drop_column("incidents", "finding_category")
