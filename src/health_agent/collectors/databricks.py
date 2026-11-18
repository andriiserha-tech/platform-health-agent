"""Databricks collector: unhealthy clusters, stuck clusters and failed job runs.

Uses the official ``databricks-sdk``. ``WorkspaceClient()`` with no arguments picks up the
notebook/job context when running on Databricks, so no host or token is needed there.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from health_agent.collectors.base import enum_str, from_epoch_ms, to_epoch_ms
from health_agent.config import Settings
from health_agent.models import CollectorResult, Finding, Severity

logger = logging.getLogger(__name__)

TRANSITIONAL_STATES = frozenset({"PENDING", "RESTARTING", "RESIZING", "TERMINATING"})
BROKEN_STATES = frozenset({"ERROR", "UNKNOWN"})
# Termination reasons that indicate a real problem (SUCCESS = normal / auto-termination).
BAD_TERMINATION_TYPES = frozenset({"CLIENT_ERROR", "SERVICE_FAULT", "CLOUD_FAILURE"})
FAILED_RESULT_STATES = frozenset({"FAILED", "TIMEDOUT", "INTERNAL_ERROR"})
OK_RESULT_STATES = frozenset({"SUCCESS", "SUCCESS_WITH_FAILURES"})


def workspace_client(settings: Settings) -> Any:
    from databricks.sdk import WorkspaceClient

    kwargs: dict[str, Any] = {}
    if settings.databricks_host:
        kwargs["host"] = settings.databricks_host
    if settings.databricks_token:
        kwargs["token"] = settings.databricks_token.get_secret_value()
    return WorkspaceClient(**kwargs)


class DatabricksCollector:
    name = "databricks"

    def __init__(
        self,
        client: Any,
        lookback: timedelta,
        stuck_after: timedelta,
    ) -> None:
        self._w = client
        self._lookback = lookback
        self._stuck_after = stuck_after

    @classmethod
    def from_settings(cls, settings: Settings, client: Any | None = None) -> DatabricksCollector:
        return cls(
            client or workspace_client(settings),
            lookback=timedelta(hours=settings.lookback_hours),
            stuck_after=timedelta(minutes=settings.cluster_stuck_minutes),
        )

    def collect(self, now: datetime) -> CollectorResult:
        clusters = list(self._w.clusters.list())
        runs = list(
            self._w.jobs.list_runs(
                completed_only=True,
                start_time_from=to_epoch_ms(now - self._lookback),
            )
        )
        logger.info("Databricks: %d clusters, %d completed job runs", len(clusters), len(runs))

        findings = [
            *self._cluster_findings(clusters, now),
            *_job_findings(runs),
        ]
        failed_runs = sum(_result_state(r) in FAILED_RESULT_STATES for r in runs)
        return CollectorResult(
            source=self.name,
            metrics={
                "clusters": len(clusters),
                "clusters_running": sum(enum_str(c.state) == "RUNNING" for c in clusters),
                "job_runs": len(runs),
                "job_runs_failed": failed_runs,
            },
            findings=findings,
        )

    # ------------------------------------------------------------------
    def _cluster_findings(self, clusters: list[Any], now: datetime) -> list[Finding]:
        findings: list[Finding] = []
        window_start = now - self._lookback
        for c in clusters:
            state = enum_str(c.state)
            name = c.cluster_name or c.cluster_id

            if state in BROKEN_STATES:
                findings.append(
                    Finding(
                        "databricks",
                        "cluster_error",
                        Severity.CRITICAL,
                        name,
                        f"state {state}: {c.state_message or 'no message'}",
                        {"cluster_id": c.cluster_id},
                    )
                )

            elif state == "TERMINATED":
                reason = c.termination_reason
                reason_type = enum_str(getattr(reason, "type", None))
                terminated_at = from_epoch_ms(c.terminated_time)
                if (
                    reason_type in BAD_TERMINATION_TYPES
                    and terminated_at is not None
                    and terminated_at >= window_start
                ):
                    code = enum_str(getattr(reason, "code", None)) or "unknown"
                    findings.append(
                        Finding(
                            "databricks",
                            "cluster_terminated_abnormally",
                            Severity.WARNING,
                            name,
                            f"terminated with {reason_type} ({code})",
                            {"cluster_id": c.cluster_id, "code": code},
                        )
                    )

            elif state in TRANSITIONAL_STATES:
                since = self._state_since(c)
                if since is None:
                    continue
                in_state = now - since
                if in_state >= self._stuck_after:
                    minutes = int(in_state.total_seconds() // 60)
                    findings.append(
                        Finding(
                            "databricks",
                            "cluster_stuck",
                            Severity.WARNING,
                            name,
                            f"{state} for {minutes} min",
                            {
                                "cluster_id": c.cluster_id,
                                "state": state,
                                "minutes": minutes,
                                "custom_tags": dict(c.custom_tags or {}),
                                "cluster_source": enum_str(c.cluster_source),
                                "last_restarted_time": c.last_restarted_time,
                            },
                        )
                    )
        return findings

    def _state_since(self, cluster: Any) -> datetime | None:
        """When the cluster entered its current state: the timestamp of its latest event.

        The clusters list API has no "state since" field, so we ask the events API — only
        for clusters in a transitional state, which keeps this to a handful of calls.
        """
        from databricks.sdk.service.compute import GetEventsOrder

        try:
            events = self._w.clusters.events(
                cluster_id=cluster.cluster_id, order=GetEventsOrder.DESC, limit=1
            )
            latest = next(iter(events), None)
        except Exception:
            logger.warning(
                "Could not read events for cluster %s", cluster.cluster_id, exc_info=True
            )
            latest = None
        if latest is not None and latest.timestamp:
            return from_epoch_ms(latest.timestamp)
        # Fallback: best available timestamp on the cluster itself.
        return from_epoch_ms(cluster.last_restarted_time or cluster.start_time)


def _result_state(run: Any) -> str | None:
    return enum_str(getattr(getattr(run, "state", None), "result_state", None))


def _job_findings(runs: list[Any]) -> list[Finding]:
    """One finding per job with failures; downgraded if the job's latest run succeeded."""
    by_job: dict[Any, list[Any]] = defaultdict(list)
    for run in runs:
        by_job[run.job_id].append(run)

    findings: list[Finding] = []
    for job_runs in by_job.values():
        job_runs.sort(key=lambda r: r.start_time or 0)
        failed = [r for r in job_runs if _result_state(r) in FAILED_RESULT_STATES]
        if not failed:
            continue
        last_failed = failed[-1]
        recovered = _result_state(job_runs[-1]) in OK_RESULT_STATES
        message = getattr(last_failed.state, "state_message", "") or ""
        findings.append(
            Finding(
                "databricks",
                "job_failed",
                Severity.WARNING if recovered else Severity.CRITICAL,
                last_failed.run_name or f"job {last_failed.job_id}",
                f"{len(failed)} failed run(s)"
                + (" — latest run succeeded" if recovered else "")
                + (f": {message[:200]}" if message else ""),
                {
                    "job_id": last_failed.job_id,
                    "run_id": last_failed.run_id,
                    "url": last_failed.run_page_url,
                },
            )
        )
    return sorted(findings, key=lambda f: f.resource)
