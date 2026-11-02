"""Data models for the factory memory lessons system.

Lessons are distilled, reviewed learnings from past runs that can be promoted
for use in future work. They require explicit human or eval approval before
promotion to prevent reinforcement of bad patterns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class LessonStatus(StrEnum):
    """Status of a lesson in the promotion workflow."""

    CANDIDATE = "candidate"  # Proposed but not yet reviewed
    PROMOTED = "promoted"  # Approved for use in future runs
    REJECTED = "rejected"  # Explicitly rejected
    EXPIRED = "expired"  # TTL exceeded without review


class Lesson(BaseModel):
    """A distilled learning from a past run.

    Lessons capture insights like "repo X needs `make gen` before tests" or
    "authentication changes require security review". They are proposed by
    agents or post-run analysis, but require explicit human or eval approval
    before being promoted for use in future runs.
    """

    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")]
    text: str = Field(min_length=1, max_length=10000, description="The lesson content")

    # Categorization and retrieval
    repo: str | None = Field(
        default=None, description="Repository URL this lesson applies to (optional)"
    )
    tags: list[str] = Field(default_factory=list, description="Tags for categorization/search")

    # Provenance: where did this lesson come from?
    run_id: str | None = Field(default=None, description="Cloud agent run that generated this")
    work_order_id: str | None = Field(default=None, description="Work order that generated this")

    # Lifecycle
    status: LessonStatus = LessonStatus.CANDIDATE
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(), description="ISO timestamp"
    )
    ttl_days: int = Field(default=90, gt=0, description="Days until expiry if not reviewed")

    # Review metadata
    reviewed_by: str | None = Field(default=None, description="Who promoted/rejected this")
    reviewed_at: str | None = Field(default=None, description="When promoted/rejected")
    review_reason: str | None = Field(default=None, description="Why promoted/rejected")

    @property
    def expires_at(self) -> datetime:
        """Calculate expiry time based on created_at + ttl_days."""
        created = datetime.fromisoformat(self.created_at)
        from datetime import timedelta

        return created + timedelta(days=self.ttl_days)

    def is_expired(self) -> bool:
        """Check if this lesson has expired without review."""
        if self.status != LessonStatus.CANDIDATE:
            return False
        return datetime.now(UTC) >= self.expires_at


class LessonProposal(BaseModel):
    """Input for proposing a new lesson."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=10000)
    repo: str | None = None
    tags: list[str] = Field(default_factory=list)
    run_id: str | None = None
    work_order_id: str | None = None
    ttl_days: int = 90


class LessonQuery(BaseModel):
    """Query parameters for searching lessons."""

    model_config = ConfigDict(extra="forbid")

    repo: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: LessonStatus | None = Field(
        default=LessonStatus.PROMOTED, description="Default to promoted-only"
    )
    include_expired: bool = Field(default=False, description="Include expired candidates")
    limit: int = Field(default=100, gt=0, le=1000)


class PromotionDecision(BaseModel):
    """Decision to promote or reject a lesson."""

    model_config = ConfigDict(extra="forbid")

    lesson_id: str = Field(min_length=1)
    approved: bool
    reason: str = Field(min_length=1, description="Why promoted/rejected")
    decided_by: str = Field(min_length=1, description="Who made this decision")
