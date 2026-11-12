"""Add reliability detection policies, assessments, and findings."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_reliability_intelligence"
down_revision: str | None = "0002_quality_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "detection_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "name", name="uq_detection_policy_dataset_name"),
    )
    op.create_index(
        "ix_detection_policies_dataset_enabled", "detection_policies", ["dataset_id", "enabled"]
    )
    op.create_table(
        "reliability_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("baseline_strategy", sa.String(20), nullable=False),
        sa.Column("baseline_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_fingerprint", sa.String(64), nullable=False),
        sa.Column("policy_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("identity_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_version_id"], ["dataset_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["policy_id"], ["detection_policies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "target_version_id", "identity_fingerprint", name="uq_reliability_assessment_identity"
        ),
    )
    op.create_index(
        "ix_reliability_assessments_dataset_target",
        "reliability_assessments",
        ["dataset_id", "target_version_id"],
    )
    op.create_table(
        "reliability_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("detector", sa.String(50), nullable=False),
        sa.Column("finding_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("column_name", sa.String(250), nullable=True),
        sa.Column("baseline_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("target_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assessment_id"], ["reliability_assessments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reliability_findings_assessment", "reliability_findings", ["assessment_id"])
    op.create_index(
        "ix_reliability_findings_detector_column",
        "reliability_findings",
        ["detector", "column_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_reliability_findings_detector_column", table_name="reliability_findings")
    op.drop_index("ix_reliability_findings_assessment", table_name="reliability_findings")
    op.drop_table("reliability_findings")
    op.drop_index("ix_reliability_assessments_dataset_target", table_name="reliability_assessments")
    op.drop_table("reliability_assessments")
    op.drop_index("ix_detection_policies_dataset_enabled", table_name="detection_policies")
    op.drop_table("detection_policies")
