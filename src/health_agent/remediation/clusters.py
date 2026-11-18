"""Guarded auto-restart of stuck Databricks clusters.

Guard-rails, all of which must pass before a cluster is touched:
  * remediation is only attempted when the caller explicitly asks (``apply=True``);
    otherwise every decision is reported as ``dry_run``.
  * the cluster opted in: custom tag ``<opt_in_tag>=true`` or its id is on the allowlist.
  * it is an interactive cluster — job clusters belong to their job run.
  * its state can actually be fixed by a restart. The Databricks restart API is a no-op unless
    the cluster is running, so only ``RESIZING`` (a running cluster) qualifies; PENDING,
    RESTARTING and TERMINATING are reported, not touched.
  * it was not restarted within the cooldown window.
  * at most ``max_restarts`` clusters per run.
A failure on one cluster is recorded and does not stop the others.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from health_agent.collectors.base import from_epoch_ms
from health_agent.config import Settings
from health_agent.models import Finding, RemediationAction

logger = logging.getLogger(__name__)

RESTARTABLE_STATES = frozenset({"RESIZING"})
JOB_SOURCES = frozenset({"JOB", "PIPELINE", "PIPELINE_MAINTENANCE"})


@dataclass(frozen=True)
class RestartPolicy:
    opt_in_tag: str = "auto_heal"
    allowlist: frozenset[str] = frozenset()
    max_restarts: int = 3
    cooldown: timedelta = timedelta(minutes=60)

    @classmethod
    def from_settings(cls, settings: Settings) -> RestartPolicy:
        return cls(
            opt_in_tag=settings.remediation_opt_in_tag,
            allowlist=settings.cluster_allowlist,
            max_restarts=settings.remediation_max_restarts_per_run,
            cooldown=timedelta(minutes=settings.remediation_cooldown_minutes),
        )

    def skip_reason(self, finding: Finding, now: datetime) -> str | None:
        d = finding.details
        tags = {k.lower(): str(v).lower() for k, v in (d.get("custom_tags") or {}).items()}
        if (
            d.get("cluster_id") not in self.allowlist
            and tags.get(self.opt_in_tag.lower()) != "true"
        ):
            return f"not opted in (tag {self.opt_in_tag}=true or allowlist)"
        if d.get("cluster_source") in JOB_SOURCES:
            return "job cluster"
        if d.get("state") not in RESTARTABLE_STATES:
            return f"restart cannot fix state {d.get('state')}"
        last = from_epoch_ms(d.get("last_restarted_time"))
        if last is not None and now - last < self.cooldown:
            return "restarted recently (cooldown)"
        return None


def remediate_stuck_clusters(
    client: Any,
    findings: list[Finding],
    policy: RestartPolicy,
    now: datetime,
    apply: bool = False,
) -> list[RemediationAction]:
    actions: list[RemediationAction] = []
    restarted = 0

    for f in findings:
        if f.source != "databricks" or f.kind != "cluster_stuck":
            continue
        cluster_id = str(f.details.get("cluster_id"))

        reason = policy.skip_reason(f, now)
        if reason is None and restarted >= policy.max_restarts:
            reason = f"per-run limit of {policy.max_restarts} reached"
        if reason is not None:
            actions.append(RemediationAction(cluster_id, f.resource, "restart", "skipped", reason))
            continue

        if not apply:
            logger.info("[dry-run] would restart cluster %s (%s)", f.resource, cluster_id)
            actions.append(RemediationAction(cluster_id, f.resource, "restart", "dry_run"))
            restarted += 1  # count dry-runs too, so the preview matches a real run
            continue

        try:
            logger.warning("Restarting cluster %s (%s)", f.resource, cluster_id)
            client.clusters.restart(cluster_id=cluster_id)
            actions.append(RemediationAction(cluster_id, f.resource, "restart", "done"))
            restarted += 1
        except Exception as exc:
            logger.exception("Restart failed for cluster %s", cluster_id)
            actions.append(
                RemediationAction(cluster_id, f.resource, "restart", "failed", str(exc)[:200])
            )
    return actions
