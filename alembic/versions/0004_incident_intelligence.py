"""Add persistent incident intelligence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_incident_intelligence"
down_revision: str | None = "0003_reliability_intelligence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "name", name="uq_incident_policy_dataset_name"),
    )
    op.create_table(
        "incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference", sa.String(32), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("severity_decision", postgresql.JSONB(), nullable=False),
        sa.Column("affected_columns", postgresql.JSONB(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_fingerprint", sa.String(64), nullable=False),
        sa.Column("merged_into_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('OPEN','ACKNOWLEDGED','INVESTIGATING','RESOLVED','REOPENED')",
            name="ck_incident_status",
        ),
        sa.CheckConstraint(
            "severity IN ('CRITICAL','HIGH','MEDIUM','LOW','INFORMATIONAL')",
            name="ck_incident_severity",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["merged_into_id"], ["incidents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference", name="uq_incidents_reference"),
    )
    op.create_index(
        "ix_incidents_dataset_status_severity", "incidents", ["dataset_id", "status", "severity"]
    )
    op.create_index(
        "ix_incidents_dataset_observed", "incidents", ["dataset_id", "last_observed_at"]
    )
    op.create_table(
        "incident_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quality_assessment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("quality_finding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reliability_assessment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reliability_finding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_type", sa.String(100), nullable=False),
        sa.Column("source_severity", sa.String(30), nullable=False),
        sa.Column("affected_columns", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("associated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('quality','reliability')", name="ck_incident_evidence_source"
        ),
        sa.CheckConstraint(
            "(source_type = 'quality' AND quality_assessment_id IS NOT NULL "
            "AND quality_finding_id IS NOT NULL AND reliability_assessment_id IS NULL "
            "AND reliability_finding_id IS NULL) OR "
            "(source_type = 'reliability' AND reliability_assessment_id IS NOT NULL "
            "AND reliability_finding_id IS NOT NULL AND quality_assessment_id IS NULL "
            "AND quality_finding_id IS NULL)",
            name="ck_incident_evidence_source_refs",
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"], ["dataset_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["quality_assessment_id"], ["quality_assessments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["quality_finding_id"], ["quality_findings.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reliability_assessment_id"],
            ["reliability_assessments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reliability_finding_id"], ["reliability_findings.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type", "finding_id", name="uq_incident_evidence_source_finding"
        ),
    )
    op.create_index(
        "ix_incident_evidence_incident_associated",
        "incident_evidence",
        ["incident_id", "associated_at"],
    )
    op.create_index("ix_incident_evidence_version", "incident_evidence", ["dataset_version_id"])
    op.create_table(
        "incident_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_value", postgresql.JSONB(), nullable=True),
        sa.Column("new_value", postgresql.JSONB(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_incident_events_incident_time", "incident_events", ["incident_id", "occurred_at", "id"]
    )
    op.create_table(
        "incident_generation_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quality_assessment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("quality_finding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reliability_assessment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reliability_finding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("qualifies", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(source_type = 'quality' AND quality_assessment_id IS NOT NULL "
            "AND quality_finding_id IS NOT NULL AND reliability_assessment_id IS NULL "
            "AND reliability_finding_id IS NULL) OR "
            "(source_type = 'reliability' AND reliability_assessment_id IS NOT NULL "
            "AND reliability_finding_id IS NOT NULL AND quality_assessment_id IS NULL "
            "AND quality_finding_id IS NULL)",
            name="ck_incident_decision_source_refs",
        ),
        sa.ForeignKeyConstraint(
            ["quality_assessment_id"], ["quality_assessments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["quality_finding_id"], ["quality_findings.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reliability_assessment_id"],
            ["reliability_assessments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reliability_finding_id"], ["reliability_findings.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type", "finding_id", name="uq_incident_decision_source_finding"
        ),
    )

    # Append-only evidence, events, and qualification decisions are protected at the DB boundary.
    op.execute("""
        CREATE FUNCTION sentinel_reject_append_only_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'incident history rows are append-only'; END;
        $$ LANGUAGE plpgsql
    """)
    for table in ("incident_evidence", "incident_events", "incident_generation_decisions"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION sentinel_reject_append_only_mutation()"
        )


def downgrade() -> None:
    for table in ("incident_generation_decisions", "incident_events", "incident_evidence"):
        op.execute(f"DROP TRIGGER {table}_append_only ON {table}")
    op.execute("DROP FUNCTION sentinel_reject_append_only_mutation()")
    op.drop_table("incident_generation_decisions")
    op.drop_index("ix_incident_events_incident_time", table_name="incident_events")
    op.drop_table("incident_events")
    op.drop_index("ix_incident_evidence_version", table_name="incident_evidence")
    op.drop_index("ix_incident_evidence_incident_associated", table_name="incident_evidence")
    op.drop_table("incident_evidence")
    op.drop_index("ix_incidents_dataset_observed", table_name="incidents")
    op.drop_index("ix_incidents_dataset_status_severity", table_name="incidents")
    op.drop_table("incidents")
    op.drop_table("incident_policies")
