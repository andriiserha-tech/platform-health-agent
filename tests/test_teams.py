from typing import Any

import requests

from health_agent.models import CollectorResult, Finding, HealthReport, RemediationAction, Severity
from health_agent.reporters.teams import MAX_FINDINGS_IN_CARD, TeamsReporter, build_payload
from tests.conftest import NOW, ns


class FakeSession:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, json: Any, timeout: float) -> Any:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return ns(status_code=r, text="body")


def reporter(responses: list[Any]) -> tuple[TeamsReporter, FakeSession, list[float]]:
    session, sleeps = FakeSession(responses), []
    rep = TeamsReporter(
        "https://hook.test",
        session=session,  # type: ignore[arg-type]
        sleep=sleeps.append,
    )
    return rep, session, sleeps


def report(n_findings: int = 1) -> HealthReport:
    findings = [
        Finding("adf", "pipeline_failed", Severity.CRITICAL, f"p{i}", "boom")
        for i in range(n_findings)
    ]
    return HealthReport(
        results=[
            CollectorResult("adf", {"total": 3}, findings),
            CollectorResult("databricks", error="RuntimeError: down"),
        ],
        remediations=[RemediationAction("c1", "cl", "restart", "dry_run")],
        generated_at=NOW,
    )


def test_accepts_202_from_workflows() -> None:
    rep, session, _ = reporter([202])
    assert rep.send(report())
    assert len(session.calls) == 1


def test_retries_on_5xx_and_network_errors_then_succeeds() -> None:
    rep, session, sleeps = reporter([500, requests.ConnectionError("x"), 200])
    assert rep.send(report())
    assert len(session.calls) == 3
    assert sleeps == [2, 4]


def test_does_not_retry_client_errors() -> None:
    rep, session, _ = reporter([400])
    assert not rep.send(report())
    assert len(session.calls) == 1


def test_gives_up_after_retries() -> None:
    rep, _, _ = reporter([503, 503, 503])
    assert not rep.send(report())


def test_payload_is_adaptive_card_with_everything() -> None:
    payload = build_payload(report(MAX_FINDINGS_IN_CARD + 5), "Title")
    attachment = payload["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard"
    texts = " ".join(str(b) for b in card["body"])
    assert "CRITICAL" in texts
    assert "RuntimeError: down" in texts
    assert "…and 5 more" in texts
    assert "restart: dry_run" in texts
