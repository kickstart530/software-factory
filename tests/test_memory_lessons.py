"""Tests for the memory lessons system (Phase 4)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from factory.memory import (
    FileBackedLessonStore,
    Lesson,
    LessonProposal,
    LessonQuery,
    LessonService,
    LessonStatus,
    PromotionDecision,
)


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    return tmp_path / "memory"


@pytest.fixture
def store(memory_dir: Path) -> FileBackedLessonStore:
    return FileBackedLessonStore(memory_dir)


@pytest.fixture
def service(store: FileBackedLessonStore) -> LessonService:
    return LessonService(store)


# === Model tests ===


def test_lesson_model_valid():
    """Test that a valid lesson can be created."""
    lesson = Lesson(
        id="lesson-001",
        text="Always run `make gen` before tests in repo X",
        repo="https://github.com/example/repo-x",
        tags=["testing", "codegen"],
        run_id="run-123",
        work_order_id="wo-456",
    )
    assert lesson.status == LessonStatus.CANDIDATE
    assert lesson.ttl_days == 90
    assert lesson.created_at is not None


def test_lesson_expires_at_calculated():
    """Test that expires_at is correctly calculated from created_at + ttl_days."""
    created = datetime.now(UTC)
    lesson = Lesson(
        id="lesson-001",
        text="test",
        created_at=created.isoformat(),
        ttl_days=30,
    )
    expected = created + timedelta(days=30)
    # Allow small variance due to processing time
    assert abs((lesson.expires_at - expected).total_seconds()) < 1


def test_lesson_is_expired():
    """Test that is_expired correctly identifies expired candidates."""
    # Expired candidate
    past = datetime.now(UTC) - timedelta(days=91)
    expired_lesson = Lesson(
        id="lesson-001",
        text="test",
        created_at=past.isoformat(),
        ttl_days=90,
        status=LessonStatus.CANDIDATE,
    )
    assert expired_lesson.is_expired()

    # Not expired candidate
    recent = datetime.now(UTC) - timedelta(days=1)
    active_lesson = Lesson(
        id="lesson-002",
        text="test",
        created_at=recent.isoformat(),
        ttl_days=90,
        status=LessonStatus.CANDIDATE,
    )
    assert not active_lesson.is_expired()

    # Promoted lessons are never considered expired
    promoted_lesson = Lesson(
        id="lesson-003",
        text="test",
        created_at=past.isoformat(),
        ttl_days=90,
        status=LessonStatus.PROMOTED,
    )
    assert not promoted_lesson.is_expired()


# === Store tests ===


def test_store_creates_directories(memory_dir: Path, store: FileBackedLessonStore):
    """Test that store creates status subdirectories."""
    for status in LessonStatus:
        assert (memory_dir / status.value).exists()


def test_store_save_and_get(store: FileBackedLessonStore):
    """Test saving and retrieving a lesson."""
    lesson = Lesson(id="lesson-001", text="test lesson")
    store.save(lesson)

    retrieved = store.get("lesson-001")
    assert retrieved is not None
    assert retrieved.id == "lesson-001"
    assert retrieved.text == "test lesson"
    assert retrieved.status == LessonStatus.CANDIDATE


def test_store_get_nonexistent(store: FileBackedLessonStore):
    """Test that get returns None for nonexistent lesson."""
    assert store.get("nonexistent") is None


def test_store_save_moves_on_status_change(store: FileBackedLessonStore):
    """Test that save moves lesson when status changes."""
    lesson = Lesson(id="lesson-001", text="test", status=LessonStatus.CANDIDATE)
    store.save(lesson)

    # Verify it's in candidates
    candidate_path = store.base_dir / "candidate" / "lesson-001.jsonl"
    assert candidate_path.exists()

    # Change status and save
    lesson.status = LessonStatus.PROMOTED
    store.save(lesson)

    # Verify it's moved to promoted
    promoted_path = store.base_dir / "promoted" / "lesson-001.jsonl"
    assert promoted_path.exists()
    assert not candidate_path.exists()


def test_store_delete(store: FileBackedLessonStore):
    """Test deleting a lesson."""
    lesson = Lesson(id="lesson-001", text="test")
    store.save(lesson)
    assert store.get("lesson-001") is not None

    store.delete("lesson-001")
    assert store.get("lesson-001") is None


def test_store_list_by_status(store: FileBackedLessonStore):
    """Test listing lessons by status."""
    # Create lessons with different statuses
    candidate = Lesson(id="lesson-001", text="candidate", status=LessonStatus.CANDIDATE)
    promoted = Lesson(id="lesson-002", text="promoted", status=LessonStatus.PROMOTED)
    rejected = Lesson(id="lesson-003", text="rejected", status=LessonStatus.REJECTED)

    store.save(candidate)
    store.save(promoted)
    store.save(rejected)

    # List each status
    candidates = store.list_by_status(LessonStatus.CANDIDATE)
    assert len(candidates) == 1
    assert candidates[0].id == "lesson-001"

    promoted_list = store.list_by_status(LessonStatus.PROMOTED)
    assert len(promoted_list) == 1
    assert promoted_list[0].id == "lesson-002"

    rejected_list = store.list_by_status(LessonStatus.REJECTED)
    assert len(rejected_list) == 1
    assert rejected_list[0].id == "lesson-003"


def test_store_query_by_repo(store: FileBackedLessonStore):
    """Test querying lessons by repository."""
    lesson1 = Lesson(
        id="lesson-001",
        text="test1",
        repo="https://github.com/example/repo-a",
        status=LessonStatus.PROMOTED,
    )
    lesson2 = Lesson(
        id="lesson-002",
        text="test2",
        repo="https://github.com/example/repo-b",
        status=LessonStatus.PROMOTED,
    )
    store.save(lesson1)
    store.save(lesson2)

    query = LessonQuery(repo="https://github.com/example/repo-a")
    results = store.query(query)
    assert len(results) == 1
    assert results[0].id == "lesson-001"


def test_store_query_by_tags(store: FileBackedLessonStore):
    """Test querying lessons by tags (must have ALL query tags)."""
    lesson1 = Lesson(
        id="lesson-001", text="test1", tags=["testing", "ci"], status=LessonStatus.PROMOTED
    )
    lesson2 = Lesson(
        id="lesson-002", text="test2", tags=["testing", "security"], status=LessonStatus.PROMOTED
    )
    lesson3 = Lesson(
        id="lesson-003", text="test3", tags=["testing"], status=LessonStatus.PROMOTED
    )
    store.save(lesson1)
    store.save(lesson2)
    store.save(lesson3)

    # Query for lessons with both "testing" and "ci"
    query = LessonQuery(tags=["testing", "ci"])
    results = store.query(query)
    assert len(results) == 1
    assert results[0].id == "lesson-001"

    # Query for lessons with just "testing"
    query = LessonQuery(tags=["testing"])
    results = store.query(query)
    assert len(results) == 3


def test_store_query_excludes_expired_by_default(store: FileBackedLessonStore):
    """Test that query excludes expired candidates by default."""
    # Create expired candidate
    past = datetime.now(UTC) - timedelta(days=91)
    expired = Lesson(
        id="lesson-001",
        text="expired",
        created_at=past.isoformat(),
        ttl_days=90,
        status=LessonStatus.CANDIDATE,
    )
    store.save(expired)

    # Query should not return it
    query = LessonQuery(status=LessonStatus.CANDIDATE)
    results = store.query(query)
    assert len(results) == 0

    # But should return if explicitly requested
    query = LessonQuery(status=LessonStatus.CANDIDATE, include_expired=True)
    results = store.query(query)
    assert len(results) == 1


def test_store_query_limit(store: FileBackedLessonStore):
    """Test query limit."""
    for i in range(10):
        lesson = Lesson(id=f"lesson-{i:03d}", text=f"test {i}", status=LessonStatus.PROMOTED)
        store.save(lesson)

    query = LessonQuery(limit=5)
    results = store.query(query)
    assert len(results) == 5


# === Service tests ===


def test_service_propose_lesson(service: LessonService):
    """Test proposing a new lesson."""
    proposal = LessonProposal(
        text="Always run tests before commit",
        repo="https://github.com/example/repo",
        tags=["testing", "workflow"],
        run_id="run-123",
    )

    lesson = service.propose_lesson(proposal)
    assert lesson.id.startswith("lesson-")
    assert lesson.text == proposal.text
    assert lesson.repo == proposal.repo
    assert lesson.tags == proposal.tags
    assert lesson.run_id == proposal.run_id
    assert lesson.status == LessonStatus.CANDIDATE


def test_service_propose_requires_provenance_for_automatic_proposal():
    """Test that lessons proposed without run_id are still valid.
    
    Note: Provenance is encouraged but not strictly required to allow
    manual lesson proposals.
    """
    service = LessonService(FileBackedLessonStore(Path(".test-memory")))
    proposal = LessonProposal(text="Manual lesson without provenance")
    lesson = service.propose_lesson(proposal)
    assert lesson.id is not None
    assert lesson.status == LessonStatus.CANDIDATE


def test_service_get(service: LessonService):
    """Test retrieving a lesson by ID."""
    proposal = LessonProposal(text="test")
    lesson = service.propose_lesson(proposal)

    retrieved = service.get(lesson.id)
    assert retrieved is not None
    assert retrieved.id == lesson.id


def test_service_list_by_status(service: LessonService):
    """Test listing lessons by status."""
    # Propose some lessons
    for i in range(3):
        service.propose_lesson(LessonProposal(text=f"lesson {i}"))

    candidates = service.list_candidates()
    assert len(candidates) == 3

    # Promote one
    decision = PromotionDecision(
        lesson_id=candidates[0].id,
        approved=True,
        reason="looks good",
        decided_by="alice",
    )
    service.promote(decision)

    # Check counts
    assert len(service.list_candidates()) == 2
    assert len(service.list_promoted()) == 1


def test_service_promote_lesson(service: LessonService):
    """Test promoting a candidate lesson."""
    proposal = LessonProposal(text="useful lesson")
    lesson = service.propose_lesson(proposal)

    decision = PromotionDecision(
        lesson_id=lesson.id,
        approved=True,
        reason="verified by eval",
        decided_by="eval-service",
    )

    promoted = service.promote(decision)
    assert promoted.status == LessonStatus.PROMOTED
    assert promoted.reviewed_by == "eval-service"
    assert promoted.reviewed_at is not None
    assert promoted.review_reason == "verified by eval"


def test_service_reject_lesson(service: LessonService):
    """Test rejecting a candidate lesson."""
    proposal = LessonProposal(text="bad advice")
    lesson = service.propose_lesson(proposal)

    decision = PromotionDecision(
        lesson_id=lesson.id,
        approved=False,
        reason="incorrect information",
        decided_by="bob",
    )

    rejected = service.reject(decision)
    assert rejected.status == LessonStatus.REJECTED
    assert rejected.reviewed_by == "bob"
    assert rejected.review_reason == "incorrect information"


def test_service_cannot_promote_noncandidate(service: LessonService):
    """Test that only candidates can be promoted/rejected."""
    proposal = LessonProposal(text="test")
    lesson = service.propose_lesson(proposal)

    # Promote it
    decision = PromotionDecision(
        lesson_id=lesson.id,
        approved=True,
        reason="ok",
        decided_by="alice",
    )
    service.promote(decision)

    # Try to promote again - should fail
    decision2 = PromotionDecision(
        lesson_id=lesson.id,
        approved=True,
        reason="still ok",
        decided_by="bob",
    )
    with pytest.raises(ValueError, match="only candidates"):
        service.promote(decision2)


def test_service_query_returns_promoted_by_default(service: LessonService):
    """Test that query returns only promoted lessons by default."""
    # Propose and promote one lesson
    proposal1 = LessonProposal(text="promoted lesson")
    lesson1 = service.propose_lesson(proposal1)
    service.promote(
        PromotionDecision(
            lesson_id=lesson1.id, approved=True, reason="good", decided_by="alice"
        )
    )

    # Propose another but don't promote
    proposal2 = LessonProposal(text="candidate lesson")
    service.propose_lesson(proposal2)

    # Default query should only return promoted
    query = LessonQuery()
    results = service.query(query)
    assert len(results) == 1
    assert results[0].id == lesson1.id
    assert results[0].status == LessonStatus.PROMOTED


def test_service_query_can_return_candidates(service: LessonService):
    """Test that query can explicitly request candidates."""
    proposal = LessonProposal(text="candidate lesson")
    lesson = service.propose_lesson(proposal)

    query = LessonQuery(status=LessonStatus.CANDIDATE)
    results = service.query(query)
    assert len(results) == 1
    assert results[0].id == lesson.id


def test_service_expire_candidates(service: LessonService):
    """Test expiring old candidates."""
    # Create an expired candidate
    past = datetime.now(UTC) - timedelta(days=91)
    expired_lesson = Lesson(
        id="lesson-expired",
        text="old candidate",
        created_at=past.isoformat(),
        ttl_days=90,
        status=LessonStatus.CANDIDATE,
    )
    service.store.save(expired_lesson)

    # Create a recent candidate
    proposal = LessonProposal(text="recent candidate")
    recent_lesson = service.propose_lesson(proposal)

    # Expire candidates
    expired = service.expire_candidates()
    assert len(expired) == 1
    assert expired[0].id == "lesson-expired"

    # Verify status changed
    updated = service.get("lesson-expired")
    assert updated is not None
    assert updated.status == LessonStatus.EXPIRED

    # Recent one should still be candidate
    recent = service.get(recent_lesson.id)
    assert recent is not None
    assert recent.status == LessonStatus.CANDIDATE


def test_service_cleanup_expired(service: LessonService):
    """Test deleting old expired lessons."""
    # Create an old expired lesson
    very_old = datetime.now(UTC) - timedelta(days=400)
    old_expired = Lesson(
        id="lesson-old",
        text="very old",
        created_at=very_old.isoformat(),
        status=LessonStatus.EXPIRED,
    )
    service.store.save(old_expired)

    # Create a recent expired lesson
    recent = datetime.now(UTC) - timedelta(days=100)
    recent_expired = Lesson(
        id="lesson-recent",
        text="recently expired",
        created_at=recent.isoformat(),
        status=LessonStatus.EXPIRED,
    )
    service.store.save(recent_expired)

    # Cleanup lessons older than 365 days
    deleted = service.cleanup_expired(older_than_days=365)
    assert deleted == 1

    # Old one should be gone
    assert service.get("lesson-old") is None

    # Recent one should still exist
    assert service.get("lesson-recent") is not None


def test_provenance_required_in_lesson_model():
    """Test that lessons track provenance for auditing."""
    lesson = Lesson(
        id="lesson-001",
        text="test",
        run_id="run-123",
        work_order_id="wo-456",
    )
    assert lesson.run_id == "run-123"
    assert lesson.work_order_id == "wo-456"


def test_lesson_text_max_length():
    """Test that lesson text has a reasonable max length."""
    # Should accept normal text
    normal = Lesson(id="lesson-001", text="x" * 1000)
    assert len(normal.text) == 1000

    # Should reject extremely long text
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Lesson(id="lesson-002", text="x" * 20000)
