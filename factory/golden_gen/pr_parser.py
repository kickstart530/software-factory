"""Parse PR metadata and diffs into structured data for golden task generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class PRMetadata:
    """Structured PR metadata for golden task generation."""

    number: int
    title: str
    description: str
    author: str
    merged_at: str
    files_changed: list[str]
    additions: int
    deletions: int
    diff: str
    labels: list[str] | None = None
    base_branch: str = "main"


def parse_pr_diff(diff_text: str) -> dict[str, str]:
    """Parse a unified diff into a map of file path to diff content.

    Args:
        diff_text: Unified diff text (output from git diff or GitHub API)

    Returns:
        Dictionary mapping file paths to their diff contents
    """
    file_diffs: dict[str, str] = {}
    current_file: str | None = None
    current_diff: list[str] = []

    for line in diff_text.split("\n"):
        # Match diff header: diff --git a/path b/path
        if line.startswith("diff --git"):
            if current_file and current_diff:
                file_diffs[current_file] = "\n".join(current_diff)

            # Extract file path from "diff --git a/path b/path"
            match = re.search(r"b/(.+)$", line)
            current_file = match.group(1) if match else None
            current_diff = [line]
        elif current_file:
            current_diff.append(line)

    # Save last file
    if current_file and current_diff:
        file_diffs[current_file] = "\n".join(current_diff)

    return file_diffs


def infer_agent_type(pr_metadata: PRMetadata) -> str:
    """Infer the most appropriate agent type based on PR characteristics.

    Args:
        pr_metadata: Parsed PR metadata

    Returns:
        Agent type name (e.g., "implementer", "reviewer", "test_engineer")
    """
    title_lower = pr_metadata.title.lower()
    desc_lower = pr_metadata.description.lower()

    # Check for review/refactor indicators
    if any(
        keyword in title_lower or keyword in desc_lower
        for keyword in ["review", "refactor", "cleanup", "improve"]
    ):
        return "implementer"

    # Check for test-related changes
    if any(
        keyword in title_lower or keyword in desc_lower for keyword in ["test", "coverage", "spec"]
    ) and all("test" in f.lower() or "spec" in f.lower() for f in pr_metadata.files_changed):
        # If only test files changed, it's test engineering
        return "test_engineer"

    # Check for documentation
    if (
        any(f.endswith((".md", ".rst", ".txt")) for f in pr_metadata.files_changed)
        and len(pr_metadata.files_changed) <= 2
    ):
        return "docs"

    # Default to implementer for most changes
    return "implementer"


def extract_instructions(pr_metadata: PRMetadata) -> str:
    """Extract task instructions from PR title and description.

    Args:
        pr_metadata: Parsed PR metadata

    Returns:
        Formatted instructions for the task
    """
    instructions = pr_metadata.title

    # If description exists and is meaningful, append it
    if pr_metadata.description and len(pr_metadata.description.strip()) > 0:
        # Clean up common GitHub PR template markers
        desc = pr_metadata.description
        desc = re.sub(r"<!--.*?-->", "", desc, flags=re.DOTALL)  # Remove HTML comments
        desc = re.sub(r"^#+\s+.*$", "", desc, flags=re.MULTILINE)  # Remove headers
        desc = desc.strip()

        if desc and len(desc) > 20:  # Only add if substantial
            instructions = f"{instructions}\n\n{desc}"

    return instructions.strip()


def estimate_complexity(pr_metadata: PRMetadata) -> dict[str, Any]:
    """Estimate task complexity metrics from PR metadata.

    Args:
        pr_metadata: Parsed PR metadata

    Returns:
        Dictionary with complexity metrics
    """
    total_changes = pr_metadata.additions + pr_metadata.deletions
    files_count = len(pr_metadata.files_changed)

    # Simple heuristic for complexity
    if total_changes < 50 and files_count <= 2:
        complexity = "low"
        suggested_budget = {"turns": 20, "usd": 5.0}
    elif total_changes < 200 and files_count <= 5:
        complexity = "medium"
        suggested_budget = {"turns": 40, "usd": 10.0}
    else:
        complexity = "high"
        suggested_budget = {"turns": 80, "usd": 20.0}

    return {
        "complexity": complexity,
        "files_count": files_count,
        "total_changes": total_changes,
        "suggested_budget": suggested_budget,
    }
