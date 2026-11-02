"""Core contracts shared by every agent, the harness and the orchestrator.

These models are the factory's public data contract. JSON Schema copies are
exported to ``schemas/`` (``factory schemas export``) for non-Python consumers.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Budget(Contract):
    """Hard caps for one task or Work Order. ``None`` means uncapped."""

    tokens: int | None = Field(default=None, gt=0)
    usd: float | None = Field(default=None, gt=0)
    wall_clock_s: int | None = Field(default=None, gt=0)
    turns: int | None = Field(default=None, gt=0)
    tool_calls: int | None = Field(default=None, gt=0)
    soft_limit_ratio: float = Field(
        default=0.8, gt=0, le=1, description="Fraction of a cap at which the agent is warned."
    )


class AcceptanceCriterion(Contract):
    id: Id
    description: str = Field(min_length=1)
    verification: Literal["gate", "review", "human"] = "gate"
    gate: str | None = Field(default=None, description="Gate name when verification == 'gate'.")

    @model_validator(mode="after")
    def _gate_named(self) -> AcceptanceCriterion:
        if self.verification == "gate" and not self.gate:
            raise ValueError("a gate-verified criterion must name its gate")
        return self


class RepoRef(Contract):
    url: str
    base_branch: str = "main"


class WorkOrder(Contract):
    """A unit of intent approved by a human: what to build and how we'll know it's done."""

    id: Id
    title: str = Field(min_length=1, max_length=200)
    description: str
    repo: RepoRef
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    risk_tier: RiskTier = RiskTier.LOW
    budget: Budget = Field(default_factory=Budget)
    requested_by: str
    labels: list[str] = Field(default_factory=list)


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class Task(Contract):
    """One independently verifiable step, executed by one agent in one sandbox."""

    id: Id
    work_order_id: Id
    agent: str = Field(description="Agent definition name, e.g. 'implementer'.")
    title: str = Field(min_length=1, max_length=200)
    instructions: str
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    depends_on: list[Id] = Field(default_factory=list)
    budget: Budget | None = Field(
        default=None, description="Overrides the agent's default budget when set."
    )
    branch: str | None = None
    status: TaskStatus = TaskStatus.PENDING


class TaskDAG(Contract):
    work_order_id: Id
    tasks: list[Task] = Field(min_length=1)

    @model_validator(mode="after")
    def _well_formed(self) -> TaskDAG:
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task ids must be unique")
        known = set(ids)
        for t in self.tasks:
            if t.work_order_id != self.work_order_id:
                raise ValueError(f"task {t.id} belongs to a different work order")
            missing = set(t.depends_on) - known
            if missing:
                raise ValueError(f"task {t.id} depends on unknown tasks: {sorted(missing)}")
        self.topological_order()  # raises on cycles
        return self

    def by_id(self) -> dict[str, Task]:
        return {t.id: t for t in self.tasks}

    def topological_order(self) -> list[str]:
        remaining = {t.id: set(t.depends_on) for t in self.tasks}
        order: list[str] = []
        while remaining:
            ready = sorted(tid for tid, deps in remaining.items() if not deps)
            if not ready:
                raise ValueError(f"dependency cycle among tasks: {sorted(remaining)}")
            for tid in ready:
                order.append(tid)
                del remaining[tid]
            for deps in remaining.values():
                deps.difference_update(ready)
        return order

    def ready(self) -> list[Task]:
        """Pending tasks whose dependencies have all succeeded."""
        done = {t.id for t in self.tasks if t.status == TaskStatus.SUCCEEDED}
        return [
            t for t in self.tasks if t.status == TaskStatus.PENDING and set(t.depends_on) <= done
        ]


class GateResult(Contract):
    name: str
    passed: bool
    exit_code: int | None = None
    output_tail: str = ""
    duration_s: float = 0.0


class Usage(Contract):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd: float = 0.0
    turns: int = 0
    tool_calls: int = 0
    wall_clock_s: float = 0.0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


class Escalation(Contract):
    reason: str
    question: str
    options: list[str] = Field(default_factory=list)


class TaskResult(Contract):
    """What the harness reports back to the orchestrator for every task run."""

    task_id: Id
    status: TaskStatus
    summary: str = ""
    output: dict | None = Field(
        default=None, description="Agent output, validated against the agent's output schema."
    )
    gates: list[GateResult] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    escalation: Escalation | None = None
    error: str | None = None
    checkpoint_id: str | None = None


# --- Agent-specific outputs -------------------------------------------------


class ImplementationResult(Contract):
    summary: str = Field(min_length=1)
    files_changed: list[str] = Field(default_factory=list)
    tests_added: list[str] = Field(default_factory=list)
    criteria_met: list[Id] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)


class Severity(StrEnum):
    BLOCKING = "blocking"
    OPTIONAL = "optional"


class Finding(Contract):
    file: str
    line: int | None = None
    severity: Severity
    category: str
    summary: str
    failure_scenario: str = ""


class ReviewReport(Contract):
    verdict: Literal["approve", "request_changes"]
    summary: str
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> ReviewReport:
        blocking = any(f.severity == Severity.BLOCKING for f in self.findings)
        if blocking and self.verdict == "approve":
            raise ValueError("cannot approve with blocking findings")
        return self


class PlanResult(Contract):
    dag: TaskDAG
    rationale: str


class IntegrationResult(Contract):
    summary: str
    root_cause: str
    commits: list[str] = Field(default_factory=list)


# --- Approval system (Phase 2b) ---------------------------------------------


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class ApprovalRequest(Contract):
    """A request for human approval of a high-risk operation."""

    id: Id
    task_id: Id
    work_order_id: Id
    agent: str
    tool_name: str
    tool_args: dict = Field(default_factory=dict)
    reason: str = Field(min_length=1, description="Why this operation needs approval")
    risk_factors: list[str] = Field(default_factory=list)
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: str = ""  # ISO timestamp, filled by approval service
    decided_at: str | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    expires_at: str | None = None


class ApprovalDecision(Contract):
    """A human's decision on an approval request."""

    request_id: Id
    approved: bool
    reason: str = Field(min_length=1)
    decided_by: str = Field(min_length=1)


# --- Policy engine (Phase 2b) -----------------------------------------------


class PolicyEvaluationInput(Contract):
    """Input bundle for external policy evaluation (e.g. OPA)."""

    agent: str
    tool_name: str
    tool_args: dict
    risk_tier: RiskTier
    work_order_id: Id
    task_id: Id
    tainted: bool
    rel_path: str | None = None
    protected_paths: dict[str, list[str]] = Field(
        default_factory=dict, description="Map of risk tier -> path patterns"
    )


class PolicyEvaluationResult(Contract):
    """Result from external policy evaluation."""

    allowed: bool
    reason: str = ""
    rule: str = ""
    requires_approval: bool = False
    approval_reason: str = ""


OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "ImplementationResult": ImplementationResult,
    "ReviewReport": ReviewReport,
    "PlanResult": PlanResult,
    "IntegrationResult": IntegrationResult,
}

EXPORTED_SCHEMAS: dict[str, type[BaseModel]] = {
    "WorkOrder": WorkOrder,
    "Task": Task,
    "TaskDAG": TaskDAG,
    "TaskResult": TaskResult,
    "ApprovalRequest": ApprovalRequest,
    "ApprovalDecision": ApprovalDecision,
    "PolicyEvaluationInput": PolicyEvaluationInput,
    "PolicyEvaluationResult": PolicyEvaluationResult,
    **OUTPUT_SCHEMAS,
}
