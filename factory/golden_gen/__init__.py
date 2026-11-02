"""Golden task generation from merged PRs.

This module provides CLI-first tools to convert merged PR diffs and metadata
into candidate golden tasks for the eval suite. Candidates require human or
eval review before promotion to the active golden set.
"""

from __future__ import annotations

from factory.golden_gen.pr_parser import PRMetadata, parse_pr_diff
from factory.golden_gen.task_generator import (
    CandidateGoldenTask,
    generate_candidate_task,
    load_candidate,
    promote_candidate,
    reject_candidate,
    save_candidate,
)

__all__ = [
    "PRMetadata",
    "parse_pr_diff",
    "CandidateGoldenTask",
    "generate_candidate_task",
    "load_candidate",
    "promote_candidate",
    "reject_candidate",
    "save_candidate",
]
