"""Structured logs and low-cardinality metrics backed by PostgreSQL job state."""

import json
import logging
from collections.abc import Iterator

from prometheus_client import CollectorRegistry
from prometheus_client.core import (
    CounterMetricFamily,
    GaugeMetricFamily,
    HistogramMetricFamily,
    Metric,
)
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from sentinel.incidents.models import IncidentRow
from sentinel.operations.models import JobRow
from sentinel.persistence.database import session_factory
from sentinel.quality.models import QualityAssessmentRow
from sentinel.reliability.models import ReliabilityAssessmentRow


class JSONLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("job_id", "correlation_id", "error_class"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONLogFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


class DatabaseMetricsCollector:
    """Expose bounded-label totals and completed job durations from durable records."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def collect(self) -> Iterator[Metric]:
        counts = CounterMetricFamily(
            "sentinel_jobs", "Persistent jobs by type and status", labels=["job_type", "status"]
        )
        active = GaugeMetricFamily(
            "sentinel_jobs_active", "Queued or running jobs", labels=["job_type"]
        )
        failed = GaugeMetricFamily(
            "sentinel_jobs_failed", "Persisted failed jobs", labels=["job_type"]
        )
        retries = CounterMetricFamily(
            "sentinel_job_retries", "Additional persisted worker attempts", labels=["job_type"]
        )
        quality = CounterMetricFamily(
            "sentinel_quality_assessments", "Quality assessments", labels=["status"]
        )
        reliability = CounterMetricFamily(
            "sentinel_reliability_assessments", "Reliability assessments", labels=["status"]
        )
        incidents = GaugeMetricFamily(
            "sentinel_incidents",
            "Incidents by lifecycle status and severity",
            labels=["status", "severity"],
        )
        histogram = HistogramMetricFamily(
            "sentinel_persisted_job_duration_seconds",
            "Observed duration from persisted jobs",
            labels=["job_type"],
        )
        with session_factory(self.engine)() as session:
            grouped = list(
                session.execute(
                    select(JobRow.job_type, JobRow.status, func.count()).group_by(
                        JobRow.job_type, JobRow.status
                    )
                )
            )
            duration_rows = list(
                session.scalars(
                    select(JobRow).where(
                        JobRow.started_at.is_not(None), JobRow.completed_at.is_not(None)
                    )
                )
            )
            for job_type, status, count in grouped:
                counts.add_metric([job_type, status], count)
            for job_type, retry_count in session.execute(
                select(
                    JobRow.job_type, func.sum(func.greatest(JobRow.attempt_count - 1, 0))
                ).group_by(JobRow.job_type)
            ):
                retries.add_metric([job_type], int(retry_count or 0))
            for row in session.execute(
                select(JobRow.job_type, func.count())
                .where(JobRow.status.in_(["QUEUED", "RUNNING"]))
                .group_by(JobRow.job_type)
            ):
                active.add_metric([row[0]], int(row[1]))
            for row in session.execute(
                select(JobRow.job_type, func.count())
                .where(JobRow.status == "FAILED")
                .group_by(JobRow.job_type)
            ):
                failed.add_metric([row[0]], int(row[1]))
            values: dict[str, list[float]] = {}
            for duration_job in duration_rows:
                if duration_job.started_at is None or duration_job.completed_at is None:
                    continue
                values.setdefault(duration_job.job_type, []).append(
                    max(
                        0.0,
                        (duration_job.completed_at - duration_job.started_at).total_seconds(),
                    )
                )
            bounds = [0.1, 1, 5, 30, 120, 600, float("inf")]
            for job_type, durations in values.items():
                buckets = [
                    (str(bound), sum(value <= bound for value in durations)) for bound in bounds
                ]
                histogram.add_metric([job_type], buckets, sum(durations), len(durations))
            for result, table in (
                (quality, QualityAssessmentRow),
                (reliability, ReliabilityAssessmentRow),
            ):
                for result_status, total in session.execute(
                    select(table.status, func.count()).group_by(table.status)
                ):
                    result.add_metric([result_status], total)
            for incident_status, severity, total in session.execute(
                select(IncidentRow.status, IncidentRow.severity, func.count()).group_by(
                    IncidentRow.status, IncidentRow.severity
                )
            ):
                incidents.add_metric([incident_status, severity], total)
        yield counts
        yield active
        yield failed
        yield retries
        yield quality
        yield reliability
        yield incidents
        yield histogram


def metrics_registry(engine: Engine) -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(DatabaseMetricsCollector(engine))
    return registry
