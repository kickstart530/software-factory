"""Storage layer for lessons: file-backed for local/test, protocol for Postgres/pgvector.

The file-backed store uses JSONL files for lessons, organized by status:
  - .factory-runs/memory/candidates/
  - .factory-runs/memory/promoted/
  - .factory-runs/memory/rejected/
  - .factory-runs/memory/expired/

This mirrors the approval service pattern and allows for easy inspection and backup.
Future stores can implement the LessonStore protocol for Postgres/pgvector.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from factory.memory.models import Lesson, LessonQuery, LessonStatus


class LessonStore(Protocol):
    """Protocol for lesson storage backends.

    This allows swapping between file-backed (local/test), Postgres, or
    pgvector implementations without changing the service layer.
    """

    def save(self, lesson: Lesson) -> None:
        """Save or update a lesson."""
        ...

    def get(self, lesson_id: str) -> Lesson | None:
        """Retrieve a lesson by ID, or None if not found."""
        ...

    def delete(self, lesson_id: str) -> None:
        """Delete a lesson by ID."""
        ...

    def list_by_status(self, status: LessonStatus) -> list[Lesson]:
        """List all lessons with the given status."""
        ...

    def query(self, query: LessonQuery) -> list[Lesson]:
        """Query lessons by repo, tags, and status.

        For v1: keyword/tag filtering only. Vector search is an optional stub.
        """
        ...


class FileBackedLessonStore:
    """File-backed lesson store using JSONL files organized by status.

    Each lesson is stored as a single-line JSON object in a JSONL file named
    after the lesson ID. Lessons are organized into subdirectories by status
    for efficient listing and lifecycle management.
    """

    def __init__(self, base_dir: Path):
        """Initialize store with base directory (e.g., .factory-runs/memory)."""
        self.base_dir = Path(base_dir)
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create status subdirectories if they don't exist."""
        for status in LessonStatus:
            (self.base_dir / status.value).mkdir(parents=True, exist_ok=True)

    def _path_for(self, lesson_id: str, status: LessonStatus) -> Path:
        """Get file path for a lesson with given ID and status."""
        return self.base_dir / status.value / f"{lesson_id}.jsonl"

    def save(self, lesson: Lesson) -> None:
        """Save or update a lesson.

        If the lesson already exists with a different status, it's moved to
        the new status directory.
        """
        # Remove from all other status directories
        for status in LessonStatus:
            if status != lesson.status:
                old_path = self._path_for(lesson.id, status)
                if old_path.exists():
                    old_path.unlink()

        # Save to the correct status directory
        path = self._path_for(lesson.id, lesson.status)
        path.write_text(lesson.model_dump_json() + "\n")

    def get(self, lesson_id: str) -> Lesson | None:
        """Retrieve a lesson by ID from any status directory."""
        for status in LessonStatus:
            path = self._path_for(lesson_id, status)
            if path.exists():
                return Lesson.model_validate_json(path.read_text())
        return None

    def delete(self, lesson_id: str) -> None:
        """Delete a lesson from all status directories."""
        for status in LessonStatus:
            path = self._path_for(lesson_id, status)
            if path.exists():
                path.unlink()

    def list_by_status(self, status: LessonStatus) -> list[Lesson]:
        """List all lessons with the given status."""
        status_dir = self.base_dir / status.value
        if not status_dir.exists():
            return []

        lessons = []
        for path in sorted(status_dir.glob("*.jsonl")):
            try:
                lesson = Lesson.model_validate_json(path.read_text())
                lessons.append(lesson)
            except Exception:
                # Skip malformed files
                continue
        return lessons

    def query(self, query: LessonQuery) -> list[Lesson]:
        """Query lessons by repo, tags, and status.

        Implements keyword/tag filtering. Results are sorted by created_at
        (most recent first) and limited per the query.
        """
        # Determine which status(es) to query
        statuses = list(LessonStatus) if query.status is None else [query.status]

        # Collect matching lessons from all relevant status directories
        results: list[Lesson] = []
        for status in statuses:
            for lesson in self.list_by_status(status):
                # Filter by expiry
                if not query.include_expired and lesson.is_expired():
                    continue

                # Filter by repo
                if query.repo and lesson.repo != query.repo:
                    continue

                # Filter by tags (lesson must have ALL query tags)
                if query.tags:
                    lesson_tags = set(lesson.tags)
                    query_tags = set(query.tags)
                    if not query_tags.issubset(lesson_tags):
                        continue

                results.append(lesson)

        # Sort by created_at (most recent first)
        results.sort(key=lambda x: x.created_at, reverse=True)

        # Apply limit
        return results[: query.limit]
