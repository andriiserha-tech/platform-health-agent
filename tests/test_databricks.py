from __future__ import annotations

from datetime import timedelta
from typing import Any

from health_agent.collectors.databricks import DatabricksCollector
from health_agent.models import Severity
from tests.conftest import NOW, ms, ns


def cluster(cid: str, state: str, **kw: Any) -> Any:
    base = dict(
        cluster_id=cid,
        cluster_name=f"name-{cid}",
        state=state,
        state_message="",
        termination_reason=None,
        terminated_time=None,
        custom_tags={},
        cluster_source="UI",
        last_restarted_time=None,
        start_time=None,
    )
    base.update(kw)
    return ns(**base)


def job_run(job_id: int, result: str, minutes_ago: int, run_id: int = 0) -> Any:
    return ns(
        job_id=job_id,
        run_id=run_id or minutes_ago,
        run_name=f"job-{job_id}",
        start_time=ms(NOW - timedelta(minutes=minutes_ago)),
        run_page_url="u",
        state=ns(result_state=result, state_message="err"),
    )


class FakeClusters:
    def __init__(self, clusters: list[Any], events: dict[str, int]) -> None:
        self._clusters = clusters
        self._events = events
        self.event_calls: list[str] = []

    def list(self) -> list[Any]:
        return self._clusters

    def events(self, cluster_id: str, **_: Any) -> list[Any]:
        self.event_calls.append(cluster_id)
        if cluster_id == "broken-events":
            raise RuntimeError("api down")
        ts = self._events.get(cluster_id)
        return [ns(timestamp=ts)] if ts else []


class FakeJobs:
    def __init__(self, runs: list[Any]) -> None:
        self._runs = runs
        self.kwargs: dict[str, Any] = {}

    def list_runs(self, **kwargs: Any) -> list[Any]:
        self.kwargs = kwargs
        return self._runs


def make(
    clusters: list[Any] = (),
    events: dict[str, int] | None = None,  # type: ignore[assignment]
    runs: list[Any] = (),
) -> tuple[DatabricksCollector, Any]:  # type: ignore[assignment]
    client = ns(clusters=FakeClusters(list(clusters), events or {}), jobs=FakeJobs(list(runs)))
    return DatabricksCollector(client, timedelta(hours=24), timedelta(minutes=20)), client


def test_stuck_cluster_uses_latest_event_time() -> None:
    c, client = make(
        [cluster("a", "RESIZING"), cluster("b", "PENDING"), cluster("c", "RUNNING")],
        events={"a": ms(NOW - timedelta(minutes=45)), "b": ms(NOW - timedelta(minutes=5))},
    )
    findings = c.collect(NOW).findings
    assert [(f.resource, f.kind) for f in findings] == [("name-a", "cluster_stuck")]
    assert findings[0].details["minutes"] == 45
    assert client.clusters.event_calls == ["a", "b"]  # only transitional clusters queried


def test_event_failure_falls_back_to_cluster_timestamps() -> None:
    c, _ = make(
        [
            cluster(
                "broken-events", "RESTARTING", last_restarted_time=ms(NOW - timedelta(minutes=30))
            )
        ]
    )
    (f,) = c.collect(NOW).findings
    assert f.details["minutes"] == 30


def test_error_state_is_critical() -> None:
    c, _ = make([cluster("x", "ERROR", state_message="driver lost")])
    (f,) = c.collect(NOW).findings
    assert f.severity is Severity.CRITICAL
    assert "driver lost" in f.message


def test_normal_termination_is_not_a_finding() -> None:
    recent = ms(NOW - timedelta(hours=1))
    c, _ = make(
        [
            cluster(
                "ok",
                "TERMINATED",
                terminated_time=recent,
                termination_reason=ns(type="SUCCESS", code="INACTIVITY"),
            ),
            cluster(
                "bad",
                "TERMINATED",
                terminated_time=recent,
                termination_reason=ns(type="CLOUD_FAILURE", code="SPOT_INSTANCE_TERMINATION"),
            ),
            cluster(
                "old",
                "TERMINATED",
                terminated_time=ms(NOW - timedelta(days=3)),
                termination_reason=ns(type="CLOUD_FAILURE", code="X"),
            ),
        ]
    )
    findings = c.collect(NOW).findings
    assert [f.resource for f in findings] == ["name-bad"]
    assert findings[0].kind == "cluster_terminated_abnormally"


def test_job_failures_grouped_and_recovery_downgrades() -> None:
    c, client = make(
        runs=[
            job_run(1, "FAILED", 60),
            job_run(1, "SUCCESS", 10),  # recovered
            job_run(2, "SUCCESS", 60),
            job_run(2, "TIMEDOUT", 10),  # broken now
            job_run(3, "SUCCESS", 5),
        ]
    )
    result = c.collect(NOW)
    assert client.jobs.kwargs["completed_only"] is True
    assert client.jobs.kwargs["start_time_from"] == ms(NOW - timedelta(hours=24))
    sev = {f.resource: f.severity for f in result.findings}
    assert sev == {"job-1": Severity.WARNING, "job-2": Severity.CRITICAL}
    assert result.metrics["job_runs"] == 5
    assert result.metrics["job_runs_failed"] == 2
