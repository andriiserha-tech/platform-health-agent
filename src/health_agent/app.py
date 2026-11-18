"""Orchestration: collectors → report → (optional) remediation → reporters.

``run_health_check`` is the public entry point, shared by the CLI, Databricks jobs and notebooks.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from health_agent.collectors.base import Collector
from health_agent.config import Settings
from health_agent.models import CollectorResult, HealthReport, Severity

logger = logging.getLogger(__name__)

NOTIFY_THRESHOLDS = {"ok": Severity.OK, "warning": Severity.WARNING, "critical": Severity.CRITICAL}


def build_collectors(settings: Settings, databricks_client: Any | None = None) -> list[Collector]:
    collectors: list[Collector] = []
    if settings.adf_enabled:
        from health_agent.collectors.adf import ADFCollector

        collectors.append(ADFCollector.from_settings(settings))
    if settings.databricks_enabled:
        from health_agent.collectors.databricks import DatabricksCollector

        collectors.append(DatabricksCollector.from_settings(settings, client=databricks_client))
    if not collectors:
        logger.warning("Nothing to monitor: neither ADF nor Databricks is configured")
    return collectors


def collect_all(collectors: list[Collector], now: datetime) -> list[CollectorResult]:
    """Run every collector; one failing source never hides the others."""
    results = []
    for collector in collectors:
        try:
            results.append(collector.collect(now))
        except Exception as exc:
            logger.exception("Collector %s failed", collector.name)
            results.append(
                CollectorResult(source=collector.name, error=f"{type(exc).__name__}: {exc}")
            )
    return results


def run_health_check(
    settings: Settings | None = None,
    *,
    remediate: bool = False,
    notify: bool = True,
    collectors: list[Collector] | None = None,
    databricks_client: Any | None = None,
    now: datetime | None = None,
) -> HealthReport:
    """Collect health, optionally restart stuck clusters, optionally notify Teams.

    Args:
        settings: defaults to ``Settings()`` (environment / .env).
        remediate: actually restart eligible stuck clusters. When False, remediation is
            still evaluated and reported as ``dry_run`` so you can see what would happen.
        notify: post to Teams (subject to ``teams_notify_on``) when a webhook is configured.
        collectors / databricks_client / now: injection points for tests.
    """
    settings = settings or Settings()
    now = now or datetime.now(UTC)

    if databricks_client is None and settings.databricks_enabled:
        from health_agent.collectors.databricks import workspace_client

        databricks_client = workspace_client(settings)

    if collectors is None:
        collectors = build_collectors(settings, databricks_client)

    report = HealthReport(results=collect_all(collectors, now), generated_at=now)

    if databricks_client is not None:
        from health_agent.remediation.clusters import RestartPolicy, remediate_stuck_clusters

        report.remediations = remediate_stuck_clusters(
            databricks_client,
            report.findings,
            RestartPolicy.from_settings(settings),
            now,
            apply=remediate,
        )

    if notify:
        _notify(settings, report)
    return report


def _notify(settings: Settings, report: HealthReport) -> None:
    if not settings.teams_webhook_url:
        logger.info("Teams webhook not configured; skipping notification")
        return
    if report.severity < NOTIFY_THRESHOLDS[settings.teams_notify_on]:
        logger.info("Status %s below notify threshold; not posting", report.severity.name)
        return
    from health_agent.reporters.teams import TeamsReporter

    TeamsReporter(
        settings.teams_webhook_url.get_secret_value(), timeout=settings.teams_timeout_seconds
    ).send(report)
