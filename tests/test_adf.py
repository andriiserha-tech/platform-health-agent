from datetime import timedelta
from typing import Any

from health_agent.collectors.adf import ADFCollector
from health_agent.models import Severity
from tests.conftest import NOW, ns


def run(name: str, status: str, minutes_ago: int, run_id: str = "", message: str = "") -> Any:
    start = NOW - timedelta(minutes=minutes_ago)
    return ns(
        pipeline_name=name,
        status=status,
        run_start=start,
        last_updated=start,
        run_id=run_id or f"{name}-{minutes_ago}",
        message=message,
    )


class FakeRuns:
    """Returns pages and records the filters it was called with."""

    def __init__(self, pages: list[list[Any]]) -> None:
        self.pages = pages
        self.tokens: list[str | None] = []

    def query_by_factory(self, rg: str, factory: str, filters: Any) -> Any:
        self.tokens.append(filters.continuation_token)
        index = len(self.tokens) - 1
        token = f"t{index + 1}" if index + 1 < len(self.pages) else None
        return ns(value=self.pages[index], continuation_token=token)


def collector(pages: list[list[Any]]) -> tuple[ADFCollector, FakeRuns]:
    runs = FakeRuns(pages)
    c = ADFCollector(
        ns(pipeline_runs=runs),
        "rg",
        "df",
        lookback=timedelta(hours=24),
        stuck_after=timedelta(minutes=60),
    )
    return c, runs


def test_follows_continuation_tokens() -> None:
    c, fake = collector([[run("a", "Succeeded", 10)], [run("b", "Succeeded", 5)], []])
    result = c.collect(NOW)
    assert fake.tokens == [None, "t1", "t2"]
    assert result.metrics["total"] == 2


def test_metrics_and_status_buckets() -> None:
    c, _ = collector(
        [
            [
                run("a", "Succeeded", 50),
                run("a", "Failed", 40),
                run("b", "Cancelled", 30),
                run("c", "Queued", 5),
                run("d", "InProgress", 5),
                run("e", "Canceling", 5),
            ]
        ]
    )
    m = c.collect(NOW).metrics
    assert m == {"total": 6, "succeeded": 1, "failed": 1, "cancelled": 1, "active": 3}


def test_unrecovered_failure_is_critical() -> None:
    c, _ = collector([[run("a", "Succeeded", 50), run("a", "Failed", 10, message="boom\n  x")]])
    (finding,) = c.collect(NOW).findings
    assert finding.kind == "pipeline_failed"
    assert finding.severity is Severity.CRITICAL
    assert "boom x" in finding.message


def test_recovered_failure_is_warning() -> None:
    c, _ = collector([[run("a", "Failed", 50), run("a", "Succeeded", 10)]])
    (finding,) = c.collect(NOW).findings
    assert finding.severity is Severity.WARNING
    assert "latest run succeeded" in finding.message


def test_latest_cancelled_is_warning_only_if_latest() -> None:
    c, _ = collector(
        [
            [
                run("a", "Cancelled", 50),
                run("a", "Succeeded", 10),
                run("b", "Succeeded", 50),
                run("b", "Cancelled", 10),
            ]
        ]
    )
    findings = c.collect(NOW).findings
    assert [(f.resource, f.kind) for f in findings] == [("b", "pipeline_cancelled")]


def test_stuck_detection_handles_naive_timestamps() -> None:
    stuck = run("slow", "InProgress", 90)
    stuck.run_start = stuck.run_start.replace(tzinfo=None)
    c, _ = collector([[stuck, run("fast", "InProgress", 30)]])
    findings = c.collect(NOW).findings
    assert [f.resource for f in findings] == ["slow"]
    assert findings[0].details["minutes"] == 90
