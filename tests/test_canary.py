"""Tests for canary deployment orchestration (Phase 4)."""

from pathlib import Path

import pytest

from factory.release import (
    CanaryOrchestrator,
    CanaryPlan,
    CanaryRun,
    CanaryStage,
    CanaryStatus,
    FakeBackend,
    HealthCheck,
    RollbackReason,
)


@pytest.fixture
def backend() -> FakeBackend:
    """Create a fake deployment backend."""
    return FakeBackend()


@pytest.fixture
def simple_plan() -> CanaryPlan:
    """Create a simple 3-stage canary plan."""
    return CanaryPlan(
        id="plan-001",
        name="standard-canary",
        stages=[
            CanaryStage(name="stage-1", traffic_percent=5, duration_seconds=60),
            CanaryStage(name="stage-2", traffic_percent=25, duration_seconds=120),
            CanaryStage(name="stage-3", traffic_percent=100, duration_seconds=180),
        ],
        health_checks=[
            HealthCheck(name="error-rate", metric="error_rate", threshold=0.05, operator="<"),
            HealthCheck(name="latency-p99", metric="latency_p99", threshold=1000.0, operator="<"),
        ],
        auto_rollback_enabled=True,
        watch_window_seconds=300,
    )


@pytest.fixture
def canary_run() -> CanaryRun:
    """Create a canary run instance."""
    return CanaryRun(
        id="run-001",
        plan_id="plan-001",
        version="v2.0.0",
        previous_version="v1.0.0",
    )


@pytest.fixture
def orchestrator(backend: FakeBackend, tmp_path: Path) -> CanaryOrchestrator:
    """Create an orchestrator with state persistence."""
    return CanaryOrchestrator(backend, state_dir=tmp_path / "canary")


def test_start_canary_marks_run_in_progress(
    orchestrator: CanaryOrchestrator, simple_plan: CanaryPlan, canary_run: CanaryRun
):
    result = orchestrator.start_canary(simple_plan, canary_run)

    assert result.status == CanaryStatus.IN_PROGRESS
    assert result.started_at is not None
    assert result.current_stage == "stage-1"
    assert len(result.decisions) == 1
    assert result.decisions[0]["decision"] == "start"


def test_cannot_start_non_pending_run(
    orchestrator: CanaryOrchestrator, simple_plan: CanaryPlan, canary_run: CanaryRun
):
    canary_run.status = CanaryStatus.IN_PROGRESS

    with pytest.raises(ValueError, match="cannot start run"):
        orchestrator.start_canary(simple_plan, canary_run)


def test_health_check_passes_with_good_metrics(
    orchestrator: CanaryOrchestrator,
    simple_plan: CanaryPlan,
    canary_run: CanaryRun,
    backend: FakeBackend,
):
    backend.simulate_healthy()
    all_passed, results = orchestrator.check_health(simple_plan, canary_run)

    assert all_passed is True
    assert len(results) == 2
    assert all(r["passed"] for r in results)


def test_health_check_fails_with_bad_metrics(
    orchestrator: CanaryOrchestrator,
    simple_plan: CanaryPlan,
    canary_run: CanaryRun,
    backend: FakeBackend,
):
    backend.simulate_unhealthy()
    all_passed, results = orchestrator.check_health(simple_plan, canary_run)

    assert all_passed is False
    assert len(results) == 2
    assert not any(r["passed"] for r in results)


def test_advance_stage_progresses_on_healthy(
    orchestrator: CanaryOrchestrator,
    simple_plan: CanaryPlan,
    canary_run: CanaryRun,
    backend: FakeBackend,
):
    backend.simulate_healthy()
    canary_run = orchestrator.start_canary(simple_plan, canary_run)

    result = orchestrator.advance_stage(simple_plan, canary_run)

    assert result.status == CanaryStatus.IN_PROGRESS
    assert result.current_stage == "stage-2"
    assert len(result.stage_executions) == 1
    assert result.stage_executions[0].status.value == "healthy"
    assert backend.deploy_calls[-1] == ("v2.0.0", 25)


def test_advance_stage_completes_after_final_stage(
    orchestrator: CanaryOrchestrator,
    simple_plan: CanaryPlan,
    canary_run: CanaryRun,
    backend: FakeBackend,
):
    backend.simulate_healthy()
    canary_run = orchestrator.start_canary(simple_plan, canary_run)

    canary_run.current_stage = "stage-3"

    result = orchestrator.advance_stage(simple_plan, canary_run)

    assert result.status == CanaryStatus.COMPLETED
    assert result.completed_at is not None


def test_advance_stage_rolls_back_on_unhealthy(
    orchestrator: CanaryOrchestrator,
    simple_plan: CanaryPlan,
    canary_run: CanaryRun,
    backend: FakeBackend,
):
    backend.simulate_healthy()
    canary_run = orchestrator.start_canary(simple_plan, canary_run)

    backend.simulate_unhealthy()

    result = orchestrator.advance_stage(simple_plan, canary_run)

    assert result.status == CanaryStatus.ROLLED_BACK
    assert result.rollback_reason == RollbackReason.HEALTH_CHECK_FAILED
    assert result.rolled_back_at is not None
    assert backend.rollback_calls == ["v1.0.0"]


def test_advance_stage_fails_without_auto_rollback(
    orchestrator: CanaryOrchestrator,
    simple_plan: CanaryPlan,
    canary_run: CanaryRun,
    backend: FakeBackend,
):
    simple_plan.auto_rollback_enabled = False
    backend.simulate_healthy()
    canary_run = orchestrator.start_canary(simple_plan, canary_run)

    backend.simulate_unhealthy()

    result = orchestrator.advance_stage(simple_plan, canary_run)

    assert result.status == CanaryStatus.FAILED
    assert len(backend.rollback_calls) == 0


def test_manual_rollback(
    orchestrator: CanaryOrchestrator, canary_run: CanaryRun, backend: FakeBackend
):
    canary_run.status = CanaryStatus.IN_PROGRESS

    result = orchestrator.rollback(canary_run, reason="operator decision")

    assert result.status == CanaryStatus.ROLLED_BACK
    assert result.rollback_reason == RollbackReason.MANUAL
    assert result.rollback_details == "operator decision"
    assert backend.rollback_calls == ["v1.0.0"]


def test_cannot_rollback_without_previous_version(
    orchestrator: CanaryOrchestrator, canary_run: CanaryRun
):
    canary_run.previous_version = None
    canary_run.status = CanaryStatus.IN_PROGRESS

    with pytest.raises(ValueError, match="no previous version"):
        orchestrator.rollback(canary_run)


def test_cannot_rollback_completed_run(orchestrator: CanaryOrchestrator, canary_run: CanaryRun):
    canary_run.status = CanaryStatus.COMPLETED

    with pytest.raises(ValueError, match="cannot rollback"):
        orchestrator.rollback(canary_run)


def test_run_full_canary_happy_path(
    orchestrator: CanaryOrchestrator,
    simple_plan: CanaryPlan,
    canary_run: CanaryRun,
    backend: FakeBackend,
):
    backend.simulate_healthy()

    result = orchestrator.run_full_canary(simple_plan, canary_run, blocking=False)

    assert result.status == CanaryStatus.COMPLETED
    assert len(result.stage_executions) == 3
    assert result.completed_at is not None
    assert all(exec.status.value == "healthy" for exec in result.stage_executions)


def test_run_full_canary_rolls_back_on_breach(
    orchestrator: CanaryOrchestrator,
    simple_plan: CanaryPlan,
    canary_run: CanaryRun,
    backend: FakeBackend,
):
    backend.simulate_healthy()

    original_check = orchestrator.check_health

    def check_and_fail(*args):
        if len(backend.deploy_calls) >= 2:
            backend.simulate_unhealthy()
        return original_check(*args)

    orchestrator.check_health = check_and_fail

    result = orchestrator.run_full_canary(simple_plan, canary_run, blocking=False)

    assert result.status == CanaryStatus.ROLLED_BACK
    assert result.rollback_reason == RollbackReason.HEALTH_CHECK_FAILED


def test_orchestrator_persists_run_state(
    orchestrator: CanaryOrchestrator, simple_plan: CanaryPlan, canary_run: CanaryRun, tmp_path: Path
):
    orchestrator.start_canary(simple_plan, canary_run)

    run_file = tmp_path / "canary" / "run-001.json"
    assert run_file.exists()

    loaded = orchestrator.load_run("run-001")
    assert loaded is not None
    assert loaded.id == "run-001"
    assert loaded.status == CanaryStatus.IN_PROGRESS


def test_list_runs_returns_sorted_runs(
    orchestrator: CanaryOrchestrator, simple_plan: CanaryPlan, backend: FakeBackend
):
    run1 = CanaryRun(id="run-001", plan_id="plan-001", version="v1.0.0")
    run2 = CanaryRun(id="run-002", plan_id="plan-001", version="v2.0.0")

    orchestrator.start_canary(simple_plan, run1)
    orchestrator.start_canary(simple_plan, run2)

    runs = orchestrator.list_runs()
    assert len(runs) == 2
    assert runs[0].id == "run-002"
    assert runs[1].id == "run-001"


def test_health_check_operators(orchestrator: CanaryOrchestrator):
    assert orchestrator._evaluate_check(10, 20, "<") is True
    assert orchestrator._evaluate_check(20, 10, "<") is False
    assert orchestrator._evaluate_check(10, 10, "<=") is True
    assert orchestrator._evaluate_check(20, 10, ">") is True
    assert orchestrator._evaluate_check(10, 20, ">") is False
    assert orchestrator._evaluate_check(10, 10, ">=") is True
    assert orchestrator._evaluate_check(10, 10, "==") is True
    assert orchestrator._evaluate_check(10, 11, "==") is False


def test_health_check_handles_missing_metric(
    orchestrator: CanaryOrchestrator,
    simple_plan: CanaryPlan,
    canary_run: CanaryRun,
    backend: FakeBackend,
):
    simple_plan.health_checks = [
        HealthCheck(name="unknown", metric="nonexistent", threshold=1.0, operator="<")
    ]

    all_passed, results = orchestrator.check_health(simple_plan, canary_run)

    assert all_passed is False
    assert len(results) == 1
    assert "error" in results[0]
    assert not results[0]["passed"]


def test_stage_gates_prevent_premature_advancement(
    orchestrator: CanaryOrchestrator,
    simple_plan: CanaryPlan,
    canary_run: CanaryRun,
    backend: FakeBackend,
):
    backend.set_metric("error_rate", 0.001)
    backend.set_metric("latency_p99", 100.0)

    canary_run = orchestrator.start_canary(simple_plan, canary_run)

    result = orchestrator.advance_stage(simple_plan, canary_run)
    assert result.current_stage == "stage-2"

    result = orchestrator.advance_stage(simple_plan, result)
    assert result.current_stage == "stage-3"

    result = orchestrator.advance_stage(simple_plan, result)
    assert result.status == CanaryStatus.COMPLETED


def test_audit_trail_records_all_decisions(
    orchestrator: CanaryOrchestrator,
    simple_plan: CanaryPlan,
    canary_run: CanaryRun,
    backend: FakeBackend,
):
    backend.simulate_healthy()

    result = orchestrator.run_full_canary(simple_plan, canary_run, blocking=False)

    assert len(result.decisions) >= 4
    decision_types = [d["decision"] for d in result.decisions]
    assert "start" in decision_types
    assert "advance" in decision_types
    assert "complete_all_stages" in decision_types


def test_deployment_failure_triggers_rollback(
    orchestrator: CanaryOrchestrator,
    simple_plan: CanaryPlan,
    canary_run: CanaryRun,
    backend: FakeBackend,
):
    backend.simulate_healthy()

    def failing_deploy(version: str, traffic: int) -> None:
        if traffic > 5:
            raise RuntimeError("deployment failed")
        backend.deploy_calls.append((version, traffic))
        backend.canary_version = version
        backend.canary_traffic_percent = traffic

    backend.deploy_canary = failing_deploy

    result = orchestrator.run_full_canary(simple_plan, canary_run, blocking=False)

    assert result.status == CanaryStatus.ROLLED_BACK
    assert "deployment failed" in result.rollback_details
