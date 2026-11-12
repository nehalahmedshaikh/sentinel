"""Add configurable quality rules, assessments, and findings."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_quality_rules"
down_revision: str | None = "0001_dataset_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quality_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("rule_type", sa.String(length=40), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "name", name="uq_quality_rule_dataset_name"),
    )
    op.create_index("ix_quality_rules_dataset_enabled", "quality_rules", ["dataset_id", "enabled"])
    op.create_table(
        "quality_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ruleset_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("rules_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("total_rules", sa.Integer(), nullable=False),
        sa.Column("passed_rules", sa.Integer(), nullable=False),
        sa.Column("failed_rules", sa.Integer(), nullable=False),
        sa.Column("error_rules", sa.Integer(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "total_rules = passed_rules + failed_rules + error_rules",
            name="ck_quality_assessment_counts",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"], ["dataset_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_version_id",
            "ruleset_fingerprint",
            name="uq_quality_assessment_version_ruleset",
        ),
    )
    op.create_index(
        "ix_quality_assessments_version_evaluated",
        "quality_assessments",
        ["dataset_version_id", "evaluated_at"],
    )
    op.create_table(
        "quality_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rule_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("evaluated_records", sa.Integer(), nullable=True),
        sa.Column("violating_records", sa.Integer(), nullable=True),
        sa.Column("violation_percentage", sa.Float(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["quality_assessments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["quality_rules.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quality_findings_assessment", "quality_findings", ["assessment_id"])
    op.create_index("ix_quality_findings_rule", "quality_findings", ["rule_id"])


def downgrade() -> None:
    op.drop_index("ix_quality_findings_rule", table_name="quality_findings")
    op.drop_index("ix_quality_findings_assessment", table_name="quality_findings")
    op.drop_table("quality_findings")
    op.drop_index("ix_quality_assessments_version_evaluated", table_name="quality_assessments")
    op.drop_table("quality_assessments")
    op.drop_index("ix_quality_rules_dataset_enabled", table_name="quality_rules")
    op.drop_table("quality_rules")
