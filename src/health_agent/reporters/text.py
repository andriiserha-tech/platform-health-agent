"""Plain-text rendering for terminals, job logs and notebook output."""

from __future__ import annotations

from health_agent.models import HealthReport, Severity

ICONS = {Severity.OK: "✅", Severity.WARNING: "⚠️", Severity.CRITICAL: "🚨"}


def render_text(report: HealthReport) -> str:
    lines = [
        f"{ICONS[report.severity]} Platform health: {report.severity.name} "
        f"({report.generated_at:%Y-%m-%d %H:%M} UTC)",
    ]
    for result in report.results:
        metrics = ", ".join(f"{k}={v}" for k, v in result.metrics.items())
        lines.append(f"\n[{result.source}] {result.severity.name}  {metrics}")
        if result.error:
            lines.append(f"  🚨 collector failed: {result.error}")
        for f in sorted(result.findings, key=lambda f: -f.severity):
            lines.append(f"  {ICONS[f.severity]} {f.resource}: {f.message}")
    if report.remediations:
        lines.append("\n[remediation]")
        for a in report.remediations:
            suffix = f" — {a.reason}" if a.reason else ""
            lines.append(f"  {a.action} {a.cluster_name}: {a.outcome}{suffix}")
    return "\n".join(lines)
