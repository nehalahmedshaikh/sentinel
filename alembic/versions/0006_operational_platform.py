"""Add persistent background jobs, schedules, and AI investigations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_operational_platform"
down_revision: str | None = "0005_incident_finding_category"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("progress", postgresql.JSONB(), nullable=False),
        sa.Column("error_class", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("idempotency_key", sa.String(300), nullable=False),
        sa.Column("correlation_id", sa.String(100), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="ck_jobs_status"
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"], ["dataset_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
    )
    op.create_index("ix_jobs_status_created", "jobs", ["status", "created_at"])
    op.create_index("ix_jobs_dataset_created", "jobs", ["dataset_id", "created_at"])
    op.create_table(
        "job_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_name", sa.String(150), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index("ix_job_outbox_pending", "job_outbox", ["published_at", "created_at"])
    op.create_table(
        "monitoring_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reliability_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("incident_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outcome", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("interval_seconds >= 60", name="ck_monitoring_interval_minimum"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["reliability_policy_id"], ["detection_policies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["incident_policy_id"], ["incident_policies.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_monitoring_schedules_due", "monitoring_schedules", ["enabled", "next_run_at"]
    )
    op.create_table(
        "schedule_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurrence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["monitoring_schedules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schedule_id", "occurrence_at", name="uq_schedule_occurrence"),
    )
    op.create_index(
        "ix_schedule_executions_history",
        "schedule_executions",
        ["schedule_id", "occurrence_at"],
    )
    op.create_table(
        "ai_investigations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=True),
        sa.Column("provider", sa.String(100), nullable=True),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("failure_class", sa.String(100), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index(
        "ix_ai_investigations_incident_created",
        "ai_investigations",
        ["incident_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_investigations_incident_created", table_name="ai_investigations")
    op.drop_table("ai_investigations")
    op.drop_index("ix_schedule_executions_history", table_name="schedule_executions")
    op.drop_table("schedule_executions")
    op.drop_index("ix_monitoring_schedules_due", table_name="monitoring_schedules")
    op.drop_table("monitoring_schedules")
    op.drop_index("ix_job_outbox_pending", table_name="job_outbox")
    op.drop_table("job_outbox")
    op.drop_index("ix_jobs_dataset_created", table_name="jobs")
    op.drop_index("ix_jobs_status_created", table_name="jobs")
    op.drop_table("jobs")
