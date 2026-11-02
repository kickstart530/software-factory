"""Core API service for factory memory lessons.

Provides high-level operations for proposing, promoting, rejecting, and
querying lessons. Enforces the promotion workflow: lessons start as candidates
and require explicit approval before becoming available for use.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from factory.memory.models import (
    Lesson,
    LessonProposal,
    LessonQuery,
    LessonStatus,
    PromotionDecision,
)
from factory.memory.store import LessonStore


class LessonService:
    """High-level API for managing lessons.

    Enforces the promotion workflow and provides query capabilities.
    Delegates storage to a LessonStore implementation.
    """

    def __init__(self, store: LessonStore):
        self.store = store

    def propose_lesson(self, proposal: LessonProposal) -> Lesson:
        """Create a new candidate lesson.

        Lessons start in CANDIDATE status and require explicit promotion
        before being included in query results (by default).
        """
        lesson_id = self._generate_id()
        lesson = Lesson(
            id=lesson_id,
            text=proposal.text,
            repo=proposal.repo,
            tags=proposal.tags,
            run_id=proposal.run_id,
            work_order_id=proposal.work_order_id,
            status=LessonStatus.CANDIDATE,
            ttl_days=proposal.ttl_days,
        )
        self.store.save(lesson)
        return lesson

    def get(self, lesson_id: str) -> Lesson | None:
        """Retrieve a lesson by ID."""
        return self.store.get(lesson_id)

    def list_candidates(self) -> list[Lesson]:
        """List all candidate lessons (not yet promoted or rejected)."""
        return self.store.list_by_status(LessonStatus.CANDIDATE)

    def list_promoted(self) -> list[Lesson]:
        """List all promoted lessons (approved for use)."""
        return self.store.list_by_status(LessonStatus.PROMOTED)

    def list_rejected(self) -> list[Lesson]:
        """List all rejected lessons."""
        return self.store.list_by_status(LessonStatus.REJECTED)

    def list_expired(self) -> list[Lesson]:
        """List all expired lessons."""
        return self.store.list_by_status(LessonStatus.EXPIRED)

    def promote(self, decision: PromotionDecision) -> Lesson:
        """Promote or reject a candidate lesson based on the decision.

        Raises:
            ValueError: If lesson not found or not in CANDIDATE status
        """
        lesson = self.store.get(decision.lesson_id)
        if lesson is None:
            raise ValueError(f"lesson {decision.lesson_id} not found")

        if lesson.status != LessonStatus.CANDIDATE:
            raise ValueError(
                f"lesson {decision.lesson_id} has status {lesson.status}, "
                "only candidates can be promoted/rejected"
            )

        # Update lesson based on decision
        lesson.status = LessonStatus.PROMOTED if decision.approved else LessonStatus.REJECTED
        lesson.reviewed_by = decision.decided_by
        lesson.reviewed_at = datetime.now(UTC).isoformat()
        lesson.review_reason = decision.reason

        self.store.save(lesson)
        return lesson

    def reject(self, decision: PromotionDecision) -> Lesson:
        """Convenience method for rejecting a lesson.

        Equivalent to promote() with approved=False.
        """
        decision.approved = False
        return self.promote(decision)

    def query(self, query: LessonQuery) -> list[Lesson]:
        """Query lessons by repo, tags, and status.

        By default, only returns promoted lessons. Expired candidates are
        excluded unless explicitly requested.
        """
        return self.store.query(query)

    def expire_candidates(self) -> list[Lesson]:
        """Mark all expired candidates as EXPIRED.

        Returns the list of newly expired lessons.
        """
        expired = []
        for lesson in self.list_candidates():
            if lesson.is_expired():
                lesson.status = LessonStatus.EXPIRED
                self.store.save(lesson)
                expired.append(lesson)
        return expired

    def cleanup_expired(self, older_than_days: int = 365) -> int:
        """Delete expired lessons older than the specified age.

        This is a maintenance operation to prevent unbounded growth.
        Returns the number of lessons deleted.
        """
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        deleted_count = 0

        for lesson in self.list_expired():
            created = datetime.fromisoformat(lesson.created_at)
            if created < cutoff:
                self.store.delete(lesson.id)
                deleted_count += 1

        return deleted_count

    def _generate_id(self) -> str:
        """Generate a unique lesson ID."""
        # Use a timestamp + random suffix for uniqueness
        ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        suffix = secrets.token_hex(4)
        return f"lesson-{ts}-{suffix}"
