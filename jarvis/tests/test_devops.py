"""tests/test_devops.py — K8s proactive health check, without hitting Grafana."""

import collections

from modules import devops, tasks


def _fake_status(deployment_phases: dict) -> dict:
    """Build a devops._pod_status_by_deployment()-shaped dict from {deployment: {phase: count}}."""
    result = {}
    for deployment, phases in deployment_phases.items():
        counter = collections.Counter()
        counter.update(phases)
        result[deployment] = counter
    return result


def test_check_k8s_health_noop_when_all_running(monkeypatch):
    monkeypatch.setattr(
        devops, "_pod_status_by_deployment", lambda ns: _fake_status({"api": {"Running": 3}})
    )
    assert devops.check_k8s_health("owner") == []
    assert tasks.list_tasks("owner") == "No pending tasks. Add one with /tasks add <title>."


def test_check_k8s_health_alerts_and_creates_task_for_unhealthy_deployment(monkeypatch):
    monkeypatch.setattr(
        devops,
        "_pod_status_by_deployment",
        lambda ns: _fake_status({"api": {"Running": 2, "CrashLoopBackOff": 1}}),
    )

    alerts = devops.check_k8s_health("owner")
    assert len(alerts) == 1
    assert "api" in alerts[0]
    assert "CrashLoopBackOff" in alerts[0]

    task_list = tasks.list_tasks("owner")
    assert "Investigate api" in task_list


def test_check_k8s_health_does_not_duplicate_task_on_repeat_poll(monkeypatch):
    monkeypatch.setattr(
        devops,
        "_pod_status_by_deployment",
        lambda ns: _fake_status({"api": {"Running": 2, "CrashLoopBackOff": 1}}),
    )

    devops.check_k8s_health("owner")
    devops.check_k8s_health("owner")  # re-alert suppressed, task not duplicated

    matches = tasks.find_pending_tasks_matching("owner", "api")
    assert len(matches) == 1


def test_check_k8s_health_noop_when_prometheus_unreachable(monkeypatch):
    monkeypatch.setattr(devops, "_pod_status_by_deployment", lambda ns: None)
    assert devops.check_k8s_health("owner") == []
