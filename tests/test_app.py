from datetime import datetime
from typing import Any

import pytest

from health_agent import app, cli
from health_agent.config import Settings
from health_agent.models import CollectorResult, Finding, Severity
from health_agent.reporters.text import render_text
from tests.conftest import NOW, ns


class StaticCollector:
    def __init__(self, name: str, severity: Severity | None = None, boom: bool = False) -> None:
        self.name = name
        self.severity = severity
        self.boom = boom

    def collect(self, now: datetime) -> CollectorResult:
        if self.boom:
            raise ConnectionError("unreachable")
        findings = [Finding(self.name, "x", self.severity, "res", "msg")] if self.severity else []
        return CollectorResult(self.name, {"n": 1}, findings)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []

    class FakeReporter:
        def __init__(self, url: str, timeout: float) -> None:
            pass

        def send(self, report: Any) -> bool:
            calls.append(report)
            return True

    import health_agent.reporters.teams as teams

    monkeypatch.setattr(teams, "TeamsReporter", FakeReporter)
    return calls


def test_failing_collector_does_not_hide_others(sent: list[Any]) -> None:
    report = app.run_health_check(
        Settings(), collectors=[StaticCollector("a", boom=True), StaticCollector("b")], now=NOW
    )
    assert [r.source for r in report.results] == ["a", "b"]
    assert report.results[0].error == "ConnectionError: unreachable"
    assert report.severity is Severity.CRITICAL
    assert "collector failed" in render_text(report)


@pytest.mark.parametrize(
    ("threshold", "severity", "expected"),
    [
        ("warning", None, 0),
        ("warning", Severity.WARNING, 1),
        ("critical", Severity.WARNING, 0),
        ("ok", None, 1),
    ],
)
def test_notify_threshold(
    sent: list[Any], threshold: str, severity: Severity | None, expected: int
) -> None:
    settings = Settings(teams_webhook_url="https://hook.test", teams_notify_on=threshold)
    app.run_health_check(settings, collectors=[StaticCollector("a", severity)], now=NOW)
    assert len(sent) == expected


def test_no_webhook_no_notification(sent: list[Any]) -> None:
    app.run_health_check(Settings(), collectors=[StaticCollector("a", Severity.CRITICAL)])
    assert sent == []


def test_remediation_runs_dry_by_default(sent: list[Any]) -> None:
    restarted: list[str] = []
    client = ns(clusters=ns(restart=lambda cluster_id: restarted.append(cluster_id)))
    finding = Finding(
        "databricks",
        "cluster_stuck",
        Severity.WARNING,
        "c",
        "m",
        {
            "cluster_id": "c1",
            "state": "RESIZING",
            "custom_tags": {"auto_heal": "true"},
        },
    )

    class C:
        name = "databricks"

        def collect(self, now: datetime) -> CollectorResult:
            return CollectorResult("databricks", findings=[finding])

    report = app.run_health_check(Settings(), collectors=[C()], databricks_client=client)
    assert [a.outcome for a in report.remediations] == ["dry_run"]
    assert restarted == []

    report = app.run_health_check(
        Settings(), collectors=[C()], databricks_client=client, remediate=True
    )
    assert restarted == ["c1"]


def test_cli_exit_code_and_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(settings: Settings, **kwargs: Any) -> Any:
        assert settings.lookback_hours == 5
        assert kwargs == {"remediate": False, "notify": False}
        return app.run_health_check(
            settings, collectors=[StaticCollector("a", Severity.WARNING)], notify=False, now=NOW
        )

    monkeypatch.setattr(cli, "run_health_check", fake_run)
    args = ["--no-notify", "--lookback-hours", "5", "--json"]
    assert cli.main([*args, "--fail-on", "warning"]) == 1
    assert '"severity": "WARNING"' in capsys.readouterr().out
    assert cli.main([*args, "--fail-on", "critical"]) == 0
    assert cli.main(args) == 0
