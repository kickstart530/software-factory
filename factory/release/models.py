"""Data models for canary deployment orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CanaryStatus(StrEnum):
    """Status of a canary deployment run."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class HealthStatus(StrEnum):
    """Health check result status."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class RollbackReason(StrEnum):
    """Reason for triggering a rollback."""

    HEALTH_CHECK_FAILED = "health_check_failed"
    ERROR_RATE_EXCEEDED = "error_rate_exceeded"
    MANUAL = "manual"
    TIMEOUT = "timeout"


class Contract(BaseModel):
    """Base contract with strict validation."""

    model_config = ConfigDict(extra="forbid")


class CanaryStage(Contract):
    """A single stage in the canary deployment progression."""

    name: str = Field(min_length=1, max_length=64)
    traffic_percent: int = Field(ge=0, le=100, description="Percentage of traffic to canary")
    duration_seconds: int = Field(
        gt=0, description="How long to observe this stage before advancing"
    )
    health_check_interval_seconds: int = Field(
        default=30, gt=0, description="How often to run health checks during this stage"
    )


class HealthCheck(Contract):
    """Configuration for a health check."""

    name: str = Field(min_length=1)
    metric: str = Field(
        min_length=1, description="Metric identifier (e.g., 'error_rate', 'latency_p99')"
    )
    threshold: float = Field(description="Threshold value; check fails if metric exceeds this")
    operator: str = Field(
        default="<", pattern="^(<|<=|>|>=|==)$", description="Comparison operator"
    )


class CanaryPlan(Contract):
    """A canary deployment plan with stages and health checks."""

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)
    stages: list[CanaryStage] = Field(min_length=1, description="Ordered list of canary stages")
    health_checks: list[HealthCheck] = Field(
        default_factory=list, description="Health checks to run at each stage"
    )
    auto_rollback_enabled: bool = Field(
        default=True, description="Whether to auto-rollback on health check failures"
    )
    watch_window_seconds: int = Field(
        default=300, gt=0, description="Post-completion watch window before marking done"
    )


class StageExecution(Contract):
    """Record of a single stage's execution."""

    stage_name: str
    traffic_percent: int
    started_at: str = Field(description="ISO 8601 timestamp")
    completed_at: str | None = None
    health_checks_passed: int = 0
    health_checks_failed: int = 0
    health_check_results: list[dict] = Field(
        default_factory=list, description="Detailed health check results"
    )
    status: HealthStatus = HealthStatus.UNKNOWN


class CanaryRun(Contract):
    """A canary deployment run instance."""

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    plan_id: str = Field(min_length=1)
    version: str = Field(
        min_length=1, description="Version/revision being deployed (e.g., git SHA, tag)"
    )
    previous_version: str | None = Field(
        default=None, description="Previous version to rollback to if needed"
    )
    status: CanaryStatus = CanaryStatus.PENDING
    current_stage: str | None = Field(default=None, description="Name of the current stage")
    started_at: str | None = Field(default=None, description="ISO 8601 timestamp")
    completed_at: str | None = None
    rolled_back_at: str | None = None
    rollback_reason: RollbackReason | None = None
    rollback_details: str | None = None
    stage_executions: list[StageExecution] = Field(
        default_factory=list, description="History of stage executions"
    )
    decisions: list[dict] = Field(
        default_factory=list,
        description="Audit trail of orchestrator decisions (timestamp, decision, reason)",
    )

    def add_decision(self, decision: str, reason: str) -> None:
        """Add a decision to the audit trail."""
        self.decisions.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "decision": decision,
                "reason": reason,
            }
        )

    def mark_started(self) -> None:
        """Mark the run as started."""
        self.status = CanaryStatus.IN_PROGRESS
        self.started_at = datetime.now(UTC).isoformat()
        self.add_decision("start", "canary deployment initiated")

    def mark_completed(self) -> None:
        """Mark the run as successfully completed."""
        self.status = CanaryStatus.COMPLETED
        self.completed_at = datetime.now(UTC).isoformat()
        self.add_decision("complete", "all stages passed; canary deployment succeeded")

    def mark_rolled_back(self, reason: RollbackReason, details: str) -> None:
        """Mark the run as rolled back."""
        self.status = CanaryStatus.ROLLED_BACK
        self.rolled_back_at = datetime.now(UTC).isoformat()
        self.rollback_reason = reason
        self.rollback_details = details
        self.add_decision("rollback", f"{reason.value}: {details}")

    def mark_failed(self, error: str) -> None:
        """Mark the run as failed."""
        self.status = CanaryStatus.FAILED
        self.completed_at = datetime.now(UTC).isoformat()
        self.add_decision("fail", error)
