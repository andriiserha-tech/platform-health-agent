from datetime import timedelta
from typing import Any

from health_agent.models import Finding, Severity
from health_agent.remediation.clusters import RestartPolicy, remediate_stuck_clusters
from tests.conftest import NOW, ms, ns


def stuck(
    cid: str,
    state: str = "RESIZING",
    tags: dict[str, str] | None = None,
    source: str = "UI",
    last_restart: int | None = None,
) -> Finding:
    return Finding(
        "databricks",
        "cluster_stuck",
        Severity.WARNING,
        f"name-{cid}",
        "stuck",
        {
            "cluster_id": cid,
            "state": state,
            "custom_tags": {"auto_heal": "true"} if tags is None else tags,
            "cluster_source": source,
            "last_restarted_time": last_restart,
        },
    )


class FakeClusters:
    def __init__(self, fail: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.restarted: list[str] = []
        self.fail = fail

    def restart(self, cluster_id: str) -> None:
        if cluster_id in self.fail:
            raise RuntimeError("nope")
        self.restarted.append(cluster_id)


def run(
    findings: list[Finding],
    apply: bool,
    policy: RestartPolicy | None = None,
    fail: set[str] = frozenset(),
) -> tuple[list[Any], FakeClusters]:  # type: ignore[assignment]
    clusters = FakeClusters(fail)
    actions = remediate_stuck_clusters(
        ns(clusters=clusters), findings, policy or RestartPolicy(), NOW, apply=apply
    )
    return actions, clusters


def test_dry_run_touches_nothing() -> None:
    actions, clusters = run([stuck("a")], apply=False)
    assert clusters.restarted == []
    assert [a.outcome for a in actions] == ["dry_run"]


def test_apply_restarts_eligible_cluster() -> None:
    actions, clusters = run([stuck("a")], apply=True)
    assert clusters.restarted == ["a"]
    assert actions[0].outcome == "done"


def test_guard_rails() -> None:
    findings = [
        stuck("no-opt-in", tags={}),
        stuck("job", source="JOB"),
        stuck("pending", state="PENDING"),
        stuck("cooldown", last_restart=ms(NOW - timedelta(minutes=10))),
    ]
    actions, clusters = run(findings, apply=True)
    assert clusters.restarted == []
    reasons = {a.cluster_id: a.reason for a in actions}
    assert "not opted in" in reasons["no-opt-in"]
    assert reasons["job"] == "job cluster"
    assert "cannot fix state PENDING" in reasons["pending"]
    assert "cooldown" in reasons["cooldown"]


def test_allowlist_counts_as_opt_in_and_tag_is_case_insensitive() -> None:
    policy = RestartPolicy(allowlist=frozenset({"listed"}))
    _, clusters = run(
        [stuck("listed", tags={}), stuck("tagged", tags={"Auto_Heal": "TRUE"})],
        apply=True,
        policy=policy,
    )
    assert clusters.restarted == ["listed", "tagged"]


def test_per_run_limit_and_failures_do_not_stop_loop() -> None:
    policy = RestartPolicy(max_restarts=2)
    actions, clusters = run(
        [stuck("a"), stuck("b"), stuck("c"), stuck("d")], apply=True, policy=policy, fail={"a"}
    )
    assert clusters.restarted == ["b", "c"]
    assert [a.outcome for a in actions] == ["failed", "done", "done", "skipped"]


def test_ignores_other_findings() -> None:
    other = Finding("databricks", "job_failed", Severity.CRITICAL, "j", "x", {})
    actions, _ = run([other], apply=True)
    assert actions == []
