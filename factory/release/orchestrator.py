"""Canary deployment orchestrator with auto-rollback."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from factory.release.models import (
    CanaryPlan,
    CanaryRun,
    CanaryStatus,
    HealthStatus,
    RollbackReason,
    StageExecution,
)

if TYPE_CHECKING:
    from factory.release.backends import DeployBackend


class CanaryOrchestrator:
    """Orchestrates canary deployment with stage progression and auto-rollback."""

    def __init__(self, backend: DeployBackend, state_dir: Path | None = None) -> None:
        """Initialize orchestrator.

        Args:
            backend: Deployment backend to use for traffic shifts and rollbacks.
            state_dir: Optional directory to persist run state (for recovery).
        """
        self.backend = backend
        self.state_dir = state_dir
        if state_dir:
            state_dir.mkdir(parents=True, exist_ok=True)

    def start_canary(self, plan: CanaryPlan, run: CanaryRun) -> CanaryRun:
        """Start a canary deployment run.

        Args:
            plan: Canary deployment plan.
            run: Canary run instance (must be in PENDING status).

        Returns:
            Updated run with IN_PROGRESS status.

        Raises:
            ValueError: If run is not in PENDING status.
        """
        if run.status != CanaryStatus.PENDING:
            raise ValueError(f"cannot start run in status {run.status}")

        run.mark_started()
        run.current_stage = plan.stages[0].name
        self._save_run(run)

        return run

    def check_health(self, plan: CanaryPlan, run: CanaryRun) -> tuple[bool, list[dict]]:
        """Run all health checks for the current stage.

        Args:
            plan: Canary plan with health check definitions.
            run: Current canary run.

        Returns:
            Tuple of (all_passed, check_results).
        """
        results = []
        all_passed = True

        for check in plan.health_checks:
            try:
                metric_value = self.backend.get_metric(check.metric)
                passed = self._evaluate_check(metric_value, check.threshold, check.operator)

                results.append(
                    {
                        "check_name": check.name,
                        "metric": check.metric,
                        "value": metric_value,
                        "threshold": check.threshold,
                        "operator": check.operator,
                        "passed": passed,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )

                if not passed:
                    all_passed = False

            except Exception as e:
                results.append(
                    {
                        "check_name": check.name,
                        "metric": check.metric,
                        "error": str(e),
                        "passed": False,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
                all_passed = False

        return all_passed, results

    def advance_stage(self, plan: CanaryPlan, run: CanaryRun) -> CanaryRun:
        """Advance the canary to the next stage after health checks pass.

        Args:
            plan: Canary plan.
            run: Current canary run.

        Returns:
            Updated run.

        Raises:
            ValueError: If run is not in IN_PROGRESS status or no current stage.
        """
        if run.status != CanaryStatus.IN_PROGRESS:
            raise ValueError(f"cannot advance run in status {run.status}")

        if not run.current_stage:
            raise ValueError("no current stage to advance from")

        current_idx = next(
            (i for i, s in enumerate(plan.stages) if s.name == run.current_stage), None
        )

        if current_idx is None:
            raise ValueError(f"current stage {run.current_stage} not found in plan")

        current_stage = plan.stages[current_idx]

        all_passed, check_results = self.check_health(plan, run)

        stage_exec = StageExecution(
            stage_name=current_stage.name,
            traffic_percent=current_stage.traffic_percent,
            started_at=run.started_at or datetime.now(UTC).isoformat(),
            completed_at=datetime.now(UTC).isoformat(),
            health_checks_passed=sum(1 for r in check_results if r.get("passed")),
            health_checks_failed=sum(1 for r in check_results if not r.get("passed")),
            health_check_results=check_results,
            status=HealthStatus.HEALTHY if all_passed else HealthStatus.UNHEALTHY,
        )
        run.stage_executions.append(stage_exec)

        if not all_passed:
            if plan.auto_rollback_enabled:
                self._perform_rollback(
                    run,
                    RollbackReason.HEALTH_CHECK_FAILED,
                    f"health checks failed at stage {current_stage.name}",
                )
            else:
                run.mark_failed(f"health checks failed at stage {current_stage.name}")
            self._save_run(run)
            return run

        if current_idx + 1 < len(plan.stages):
            next_stage = plan.stages[current_idx + 1]
            run.current_stage = next_stage.name
            run.add_decision(
                "advance",
                f"advanced from {current_stage.name} to {next_stage.name} "
                f"({next_stage.traffic_percent}% traffic)",
            )

            try:
                self.backend.deploy_canary(run.version, next_stage.traffic_percent)
            except Exception as e:
                if plan.auto_rollback_enabled:
                    self._perform_rollback(
                        run,
                        RollbackReason.HEALTH_CHECK_FAILED,
                        f"deployment failed: {e}",
                    )
                else:
                    run.mark_failed(f"deployment failed: {e}")
                self._save_run(run)
                return run

        else:
            run.mark_completed()
            run.add_decision(
                "complete_all_stages", "all stages completed successfully at 100% traffic"
            )

        self._save_run(run)
        return run

    def rollback(self, run: CanaryRun, reason: str = "manual") -> CanaryRun:
        """Manually trigger a rollback.

        Args:
            run: Canary run to rollback.
            reason: Human-readable reason for rollback.

        Returns:
            Updated run with ROLLED_BACK status.

        Raises:
            ValueError: If run is already completed/rolled back, or no previous version.
        """
        if run.status in (CanaryStatus.COMPLETED, CanaryStatus.ROLLED_BACK):
            raise ValueError(f"cannot rollback run in status {run.status}")

        if not run.previous_version:
            raise ValueError("no previous version to rollback to")

        return self._perform_rollback(run, RollbackReason.MANUAL, reason)

    def run_full_canary(self, plan: CanaryPlan, run: CanaryRun, blocking: bool = True) -> CanaryRun:
        """Execute a complete canary deployment with all stages.

        Args:
            plan: Canary plan.
            run: Canary run instance (must be PENDING).
            blocking: If True, wait for each stage's duration; if False, just execute once.

        Returns:
            Final run state.
        """
        run = self.start_canary(plan, run)

        try:
            self.backend.deploy_canary(run.version, plan.stages[0].traffic_percent)
        except Exception as e:
            if plan.auto_rollback_enabled and run.previous_version:
                return self._perform_rollback(
                    run, RollbackReason.HEALTH_CHECK_FAILED, f"initial deployment failed: {e}"
                )
            run.mark_failed(f"initial deployment failed: {e}")
            self._save_run(run)
            return run

        for stage in plan.stages:
            if blocking:
                time.sleep(stage.duration_seconds)

            run = self.advance_stage(plan, run)

            if run.status in (CanaryStatus.ROLLED_BACK, CanaryStatus.FAILED):
                return run

        return run

    def _perform_rollback(self, run: CanaryRun, reason: RollbackReason, details: str) -> CanaryRun:
        """Perform the actual rollback operation."""
        if not run.previous_version:
            run.mark_failed(f"rollback required but no previous version available: {details}")
            self._save_run(run)
            return run

        try:
            self.backend.rollback(run.previous_version)
            run.mark_rolled_back(reason, details)
        except Exception as e:
            run.mark_failed(f"rollback failed: {e}")

        self._save_run(run)
        return run

    def _evaluate_check(self, value: float, threshold: float, operator: str) -> bool:
        """Evaluate a health check condition."""
        match operator:
            case "<":
                return value < threshold
            case "<=":
                return value <= threshold
            case ">":
                return value > threshold
            case ">=":
                return value >= threshold
            case "==":
                return value == threshold
            case _:
                raise ValueError(f"unknown operator: {operator}")

    def _save_run(self, run: CanaryRun) -> None:
        """Save run state to disk if state_dir is configured."""
        if self.state_dir:
            run_file = self.state_dir / f"{run.id}.json"
            run_file.write_text(run.model_dump_json(indent=2))

    def load_run(self, run_id: str) -> CanaryRun | None:
        """Load a run from disk.

        Args:
            run_id: Run identifier.

        Returns:
            CanaryRun if found, None otherwise.
        """
        if not self.state_dir:
            return None

        run_file = self.state_dir / f"{run_id}.json"
        if not run_file.exists():
            return None

        return CanaryRun.model_validate_json(run_file.read_text())

    def list_runs(self) -> list[CanaryRun]:
        """List all runs from state directory.

        Returns:
            List of all canary runs, sorted by started_at (newest first).
        """
        if not self.state_dir or not self.state_dir.exists():
            return []

        runs = []
        for run_file in self.state_dir.glob("*.json"):
            try:
                run = CanaryRun.model_validate_json(run_file.read_text())
                runs.append(run)
            except Exception:
                continue

        return sorted(runs, key=lambda r: r.started_at or "", reverse=True)
