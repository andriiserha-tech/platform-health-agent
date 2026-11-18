"""Azure Data Factory collector: failed, cancelled and stuck pipeline runs."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from health_agent.collectors.base import as_utc
from health_agent.config import Settings
from health_agent.models import CollectorResult, Finding, Severity

logger = logging.getLogger(__name__)


def azure_credential(settings: Settings) -> Any:
    """Pick the Azure login for ADF, in order of preference:

    1. Unity Catalog service credential (on Databricks): a managed identity, no secret to rotate.
    2. Service principal (tenant id + client id + secret).
    3. DefaultAzureCredential: az login locally, managed identity on Azure compute.
    """
    if settings.azure_service_credential:
        from health_agent.databricks_utils import get_dbutils

        logger.info("ADF auth: service credential %r", settings.azure_service_credential)
        return get_dbutils().credentials.getServiceCredentialsProvider(
            settings.azure_service_credential
        )

    from azure.identity import ClientSecretCredential, DefaultAzureCredential

    if settings.azure_client_id and settings.azure_client_secret and settings.azure_tenant_id:
        logger.info("ADF auth: service principal %s", settings.azure_client_id)
        return ClientSecretCredential(
            tenant_id=settings.azure_tenant_id,
            client_id=settings.azure_client_id,
            client_secret=settings.azure_client_secret.get_secret_value(),
        )
    logger.info("ADF auth: DefaultAzureCredential")
    return DefaultAzureCredential()


FAILED = "Failed"
CANCELLED = "Cancelled"
SUCCEEDED = "Succeeded"
ACTIVE = frozenset({"Queued", "InProgress", "Canceling"})


class ADFCollector:
    name = "adf"

    def __init__(
        self,
        client: Any,
        resource_group: str,
        factory_name: str,
        lookback: timedelta,
        stuck_after: timedelta,
    ) -> None:
        self._client = client
        self._resource_group = resource_group
        self._factory_name = factory_name
        self._lookback = lookback
        self._stuck_after = stuck_after

    @classmethod
    def from_settings(cls, settings: Settings) -> ADFCollector:
        from azure.mgmt.datafactory import DataFactoryManagementClient

        assert settings.adf_subscription_id and settings.adf_resource_group
        assert settings.adf_factory_name
        client = DataFactoryManagementClient(
            azure_credential(settings), settings.adf_subscription_id
        )
        return cls(
            client,
            settings.adf_resource_group,
            settings.adf_factory_name,
            lookback=timedelta(hours=settings.lookback_hours),
            stuck_after=timedelta(minutes=settings.adf_stuck_minutes),
        )

    # ------------------------------------------------------------------
    def fetch_runs(self, now: datetime) -> list[Any]:
        """Fetch ALL runs in the window, following continuation tokens."""
        from azure.mgmt.datafactory.models import RunFilterParameters

        filters = RunFilterParameters(
            last_updated_after=now - self._lookback,
            last_updated_before=now,
        )
        runs: list[Any] = []
        while True:
            page = self._client.pipeline_runs.query_by_factory(
                self._resource_group, self._factory_name, filters
            )
            runs.extend(page.value or [])
            if not page.continuation_token:
                break
            filters.continuation_token = page.continuation_token
        logger.info("ADF: fetched %d pipeline runs", len(runs))
        return runs

    def collect(self, now: datetime) -> CollectorResult:
        runs = self.fetch_runs(now)
        return CollectorResult(
            source=self.name,
            metrics=_count_statuses(runs),
            findings=[*_failure_findings(runs), *_stuck_findings(runs, now, self._stuck_after)],
        )


# ----------------------------------------------------------------------
# Pure analysis helpers (unit-tested without the SDK)
# ----------------------------------------------------------------------
def _count_statuses(runs: list[Any]) -> dict[str, int]:
    return {
        "total": len(runs),
        "succeeded": sum(r.status == SUCCEEDED for r in runs),
        "failed": sum(r.status == FAILED for r in runs),
        "cancelled": sum(r.status == CANCELLED for r in runs),
        "active": sum(r.status in ACTIVE for r in runs),
    }


def _run_sort_key(run: Any) -> datetime:
    ts = run.run_start or run.last_updated
    return as_utc(ts) if ts else datetime.min.replace(tzinfo=UTC)


def _failure_findings(runs: list[Any]) -> list[Finding]:
    """One finding per pipeline with failures; downgrade if its latest run has since succeeded."""
    by_pipeline: dict[str, list[Any]] = defaultdict(list)
    for run in runs:
        by_pipeline[run.pipeline_name].append(run)

    findings: list[Finding] = []
    for pipeline, pipeline_runs in sorted(by_pipeline.items()):
        pipeline_runs.sort(key=_run_sort_key)
        failed = [r for r in pipeline_runs if r.status == FAILED]
        cancelled = [r for r in pipeline_runs if r.status == CANCELLED]
        latest = pipeline_runs[-1]

        if failed:
            recovered = latest.status == SUCCEEDED
            last_failure = failed[-1]
            findings.append(
                Finding(
                    source="adf",
                    kind="pipeline_failed",
                    severity=Severity.WARNING if recovered else Severity.CRITICAL,
                    resource=pipeline,
                    message=(
                        f"{len(failed)} failed run(s)"
                        + (" — latest run succeeded" if recovered else "")
                        + (f": {_short(last_failure.message)}" if last_failure.message else "")
                    ),
                    details={"run_id": last_failure.run_id, "failed_count": len(failed)},
                )
            )
        elif cancelled and latest.status == CANCELLED:
            findings.append(
                Finding(
                    source="adf",
                    kind="pipeline_cancelled",
                    severity=Severity.WARNING,
                    resource=pipeline,
                    message=f"latest run was cancelled ({len(cancelled)} cancelled in window)",
                    details={"run_id": latest.run_id},
                )
            )
    return findings


def _stuck_findings(runs: list[Any], now: datetime, stuck_after: timedelta) -> list[Finding]:
    findings: list[Finding] = []
    for run in runs:
        if run.status not in ACTIVE or not run.run_start:
            continue
        running_for = now - as_utc(run.run_start)
        if running_for >= stuck_after:
            minutes = int(running_for.total_seconds() // 60)
            findings.append(
                Finding(
                    source="adf",
                    kind="pipeline_stuck",
                    severity=Severity.WARNING,
                    resource=run.pipeline_name,
                    message=f"{run.status} for {minutes} min",
                    details={"run_id": run.run_id, "minutes": minutes},
                )
            )
    return findings


def _short(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
