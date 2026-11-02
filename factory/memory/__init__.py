"""Factory memory lessons: distilled, reviewed learnings with promotion workflow.

Public API:
    - LessonService: propose, list, promote, reject, query, expire, cleanup
    - FileBackedLessonStore: file-based storage implementation
    - Models: Lesson, LessonProposal, LessonQuery, PromotionDecision, LessonStatus
"""

from factory.memory.models import (
    Lesson,
    LessonProposal,
    LessonQuery,
    LessonStatus,
    PromotionDecision,
)
from factory.memory.service import LessonService
from factory.memory.store import FileBackedLessonStore, LessonStore

__all__ = [
    # Service
    "LessonService",
    # Store
    "LessonStore",
    "FileBackedLessonStore",
    # Models
    "Lesson",
    "LessonProposal",
    "LessonQuery",
    "PromotionDecision",
    "LessonStatus",
]
