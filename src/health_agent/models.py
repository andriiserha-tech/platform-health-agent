"""Shared data model: every collector returns the same shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any


class Severity(IntEnum):
    """Ordered so that max() gives the worst severity."""

    OK = 0
    WARNING = 1
    CRITICAL = 2


@dataclass(frozen=True)
class Finding:
    source: str  # "adf" | "databricks"
    kind: str  # e.g. "pipeline_failed", "cluster_stuck"
    severity: Severity
    resource: str  # human-readable name
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectorResult:
    source: str
    metrics: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None  # set when the collector itself failed

    @property
    def severity(self) -> Severity:
        if self.error:
            return Severity.CRITICAL
        return max((f.severity for f in self.findings), default=Severity.OK)


@dataclass(frozen=True)
class RemediationAction:
    cluster_id: str
    cluster_name: str
    action: str  # "restart"
    outcome: str  # "dry_run" | "done" | "failed" | "skipped"
    reason: str = ""


@dataclass
class HealthReport:
    results: list[CollectorResult]
    remediations: list[RemediationAction] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def severity(self) -> Severity:
        return max((r.severity for r in self.results), default=Severity.OK)

    @property
    def findings(self) -> list[Finding]:
        return [f for r in self.results for f in r.findings]

    def to_rows(self) -> list[dict[str, Any]]:
        """Flat rows, e.g. for ``spark.createDataFrame(report.to_rows())`` in a notebook."""
        return [
            {
                "generated_at": self.generated_at,
                "source": f.source,
                "kind": f.kind,
                "severity": f.severity.name,
                "resource": f.resource,
                "message": f.message,
            }
            for f in self.findings
        ]
