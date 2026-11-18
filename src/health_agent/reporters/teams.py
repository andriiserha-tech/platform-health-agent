"""Microsoft Teams reporter using an Adaptive Card.

Targets Teams *Workflows* webhooks ("Post to a channel when a webhook request is received"),
which replaced the retired Office 365 connectors / MessageCard format. Workflows answer 202.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from health_agent.models import HealthReport, Severity

logger = logging.getLogger(__name__)

MAX_FINDINGS_IN_CARD = 25
COLORS = {Severity.OK: "Good", Severity.WARNING: "Warning", Severity.CRITICAL: "Attention"}
ICONS = {Severity.OK: "✅", Severity.WARNING: "⚠️", Severity.CRITICAL: "🚨"}


class TeamsReporter:
    def __init__(
        self,
        webhook_url: str,
        timeout: float = 10,
        retries: int = 3,
        session: requests.Session | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._url = webhook_url
        self._timeout = timeout
        self._retries = retries
        self._session = session or requests.Session()
        self._sleep = sleep

    def send(self, report: HealthReport, title: str = "Data platform health") -> bool:
        return self._post(build_payload(report, title))

    def _post(self, payload: dict[str, Any]) -> bool:
        for attempt in range(1, self._retries + 1):
            try:
                resp = self._session.post(self._url, json=payload, timeout=self._timeout)
                if 200 <= resp.status_code < 300:
                    logger.info("Teams notification sent (%s)", resp.status_code)
                    return True
                # 4xx other than throttling won't fix itself — don't retry.
                retryable = resp.status_code == 429 or resp.status_code >= 500
                logger.warning("Teams webhook returned %s: %s", resp.status_code, resp.text[:300])
                if not retryable:
                    return False
            except requests.RequestException as exc:
                logger.warning("Teams send attempt %d failed: %s", attempt, exc)
            if attempt < self._retries:
                self._sleep(2**attempt)
        logger.error("Giving up on Teams notification after %d attempts", self._retries)
        return False


def build_payload(report: HealthReport, title: str) -> dict[str, Any]:
    severity = report.severity
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "color": COLORS[severity],
            "text": f"{ICONS[severity]} {title}: {severity.name}",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "isSubtle": True,
            "spacing": "None",
            "text": f"{report.generated_at:%Y-%m-%d %H:%M} UTC",
        },
    ]

    for result in report.results:
        facts = [{"title": k, "value": str(v)} for k, v in result.metrics.items()]
        if result.error:
            facts.append({"title": "error", "value": result.error[:300]})
        body.append(
            {
                "type": "TextBlock",
                "weight": "Bolder",
                "separator": True,
                "color": COLORS[result.severity],
                "text": f"{result.source.upper()} — {result.severity.name}",
            }
        )
        if facts:
            body.append({"type": "FactSet", "facts": facts})

    findings = sorted(report.findings, key=lambda f: (-f.severity, f.source, f.resource))
    if findings:
        body.append(
            {"type": "TextBlock", "weight": "Bolder", "separator": True, "text": "Findings"}
        )
        for f in findings[:MAX_FINDINGS_IN_CARD]:
            body.append(
                {
                    "type": "TextBlock",
                    "wrap": True,
                    "spacing": "Small",
                    "text": f"{ICONS[f.severity]} **{f.resource}** ({f.source}): {f.message}",
                }
            )
        if len(findings) > MAX_FINDINGS_IN_CARD:
            body.append(
                {
                    "type": "TextBlock",
                    "isSubtle": True,
                    "text": f"…and {len(findings) - MAX_FINDINGS_IN_CARD} more",
                }
            )

    if report.remediations:
        body.append(
            {"type": "TextBlock", "weight": "Bolder", "separator": True, "text": "Remediation"}
        )
        body.append(
            {
                "type": "FactSet",
                "facts": [
                    {
                        "title": a.cluster_name,
                        "value": f"{a.action}: {a.outcome}"
                        + (f" ({a.reason})" if a.reason else ""),
                    }
                    for a in report.remediations
                ],
            }
        )

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "msteams": {"width": "Full"},
                    "body": body,
                },
            }
        ],
    }
