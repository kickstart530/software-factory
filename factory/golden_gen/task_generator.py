"""Generate candidate golden tasks from PR metadata."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factory.golden_gen.pr_parser import (
    PRMetadata,
    estimate_complexity,
    extract_instructions,
    infer_agent_type,
)


@dataclass
class CandidateGoldenTask:
    """A candidate golden task awaiting review and promotion."""

    id: str
    pr_number: int
    description: str
    task: dict[str, Any]
    agent_spec: dict[str, Any]
    expected_status: str
    expected_gates: list[str]
    metadata: dict[str, Any]
    created_at: str
    status: str = "pending_review"  # pending_review, approved, rejected
    review_notes: str | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None


def generate_candidate_task(
    pr_metadata: PRMetadata,
    agent_catalog_path: Path | None = None,
    custom_agent: str | None = None,
) -> CandidateGoldenTask:
    """Generate a candidate golden task from PR metadata.

    Args:
        pr_metadata: Parsed PR metadata including diff
        agent_catalog_path: Path to agents directory (for loading agent specs)
        custom_agent: Override inferred agent type

    Returns:
        Candidate golden task ready for review
    """
    # Infer or use custom agent type
    agent_type = custom_agent or infer_agent_type(pr_metadata)

    # Extract task details
    instructions = extract_instructions(pr_metadata)
    complexity = estimate_complexity(pr_metadata)

    # Generate unique task ID
    task_id = f"candidate_{pr_metadata.number}_{uuid.uuid4().hex[:8]}"

    # Create task structure
    task = {
        "id": task_id,
        "work_order_id": f"WO_PR_{pr_metadata.number}",
        "agent": agent_type,
        "title": pr_metadata.title,
        "instructions": instructions,
    }

    # Create agent spec (minimal version - will be enriched from catalog if available)
    agent_spec = {
        "name": agent_type,
        "version": 1,
        "description": f"Agent for {agent_type} tasks",
        "model_tier": "frontier",
        "system_prompt": f"prompts/{agent_type}.md",
        "tools": _get_default_tools(agent_type),
        "budget": complexity["suggested_budget"],
        "gates": [],  # Will be populated based on repo's .factory/gates.yaml
        "max_gate_attempts": 3,
        "output_schema": _get_output_schema(agent_type),
    }

    # If agent catalog exists, try to load actual agent spec
    if agent_catalog_path and agent_catalog_path.exists():
        try:
            from factory.agents.spec import AgentCatalog

            catalog = AgentCatalog(agent_catalog_path)
            if agent_type in catalog._specs:
                real_spec = catalog[agent_type]
                agent_spec = {
                    "name": real_spec.name,
                    "version": real_spec.version,
                    "description": real_spec.description,
                    "model_tier": real_spec.model_tier,
                    "system_prompt": real_spec.system_prompt,
                    "tools": real_spec.tools,
                    "budget": real_spec.budget.model_dump(),
                    "gates": real_spec.gates,
                    "max_gate_attempts": real_spec.max_gate_attempts,
                    "output_schema": real_spec.output_schema,
                }
        except Exception:
            # Fall back to defaults if catalog loading fails
            pass

    # Build metadata
    metadata = {
        "source_pr": {
            "number": pr_metadata.number,
            "title": pr_metadata.title,
            "author": pr_metadata.author,
            "merged_at": pr_metadata.merged_at,
            "base_branch": pr_metadata.base_branch,
            "labels": pr_metadata.labels or [],
        },
        "complexity": complexity,
        "files_changed": pr_metadata.files_changed,
        "diff_summary": {
            "additions": pr_metadata.additions,
            "deletions": pr_metadata.deletions,
            "total": pr_metadata.additions + pr_metadata.deletions,
        },
        "generation_timestamp": datetime.now(UTC).isoformat(),
    }

    return CandidateGoldenTask(
        id=task_id,
        pr_number=pr_metadata.number,
        description=f"Golden task from PR #{pr_metadata.number}: {pr_metadata.title}",
        task=task,
        agent_spec=agent_spec,
        expected_status="succeeded",  # PRs that merged are assumed successful
        expected_gates=[],  # TODO: Could be inferred from CI results
        metadata=metadata,
        created_at=datetime.now(UTC).isoformat(),
    )


def _get_default_tools(agent_type: str) -> list[str]:
    """Get default tools for an agent type."""
    base_tools = ["read_file", "submit_result"]

    if agent_type in ("implementer", "test_engineer"):
        return base_tools + ["write_file", "edit_file", "run_command"]
    elif agent_type == "reviewer":
        return base_tools + ["search_code"]
    elif agent_type == "docs":
        return base_tools + ["write_file", "edit_file"]
    else:
        return base_tools + ["edit_file"]


def _get_output_schema(agent_type: str) -> str:
    """Get output schema for an agent type."""
    schema_map = {
        "implementer": "ImplementationResult",
        "reviewer": "ReviewReport",
        "test_engineer": "ImplementationResult",
        "docs": "ImplementationResult",
    }
    return schema_map.get(agent_type, "TaskResult")


def save_candidate(candidate: CandidateGoldenTask, candidates_dir: Path) -> Path:
    """Save candidate golden task to the candidates directory.

    Args:
        candidate: Candidate golden task to save
        candidates_dir: Directory for candidate tasks

    Returns:
        Path to saved candidate file
    """
    candidates_dir.mkdir(parents=True, exist_ok=True)

    # Create filename from PR number and timestamp
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"pr_{candidate.pr_number}_{timestamp}.json"
    filepath = candidates_dir / filename

    # Convert to JSON-serializable dict
    data = {
        "id": candidate.id,
        "pr_number": candidate.pr_number,
        "description": candidate.description,
        "task": candidate.task,
        "agent_spec": candidate.agent_spec,
        "expected_status": candidate.expected_status,
        "expected_gates": candidate.expected_gates,
        "metadata": candidate.metadata,
        "created_at": candidate.created_at,
        "status": candidate.status,
        "review_notes": candidate.review_notes,
        "reviewer": candidate.reviewer,
        "reviewed_at": candidate.reviewed_at,
    }

    filepath.write_text(json.dumps(data, indent=2) + "\n")
    return filepath


def load_candidate(filepath: Path) -> CandidateGoldenTask:
    """Load a candidate golden task from file.

    Args:
        filepath: Path to candidate JSON file

    Returns:
        Loaded candidate golden task
    """
    data = json.loads(filepath.read_text())
    return CandidateGoldenTask(**data)


def promote_candidate(
    candidate: CandidateGoldenTask,
    golden_dir: Path,
    candidates_dir: Path,
    reviewer: str,
    review_notes: str | None = None,
) -> Path:
    """Promote an approved candidate to the active golden set.

    Args:
        candidate: Candidate to promote
        golden_dir: Directory for active golden tasks
        candidates_dir: Directory where candidates are stored
        reviewer: Name of person approving the promotion
        review_notes: Optional notes about the review

    Returns:
        Path to promoted golden task file

    Raises:
        ValueError: If candidate is not approved
    """
    if candidate.status not in ("pending_review", "approved"):
        raise ValueError(
            f"Cannot promote candidate with status '{candidate.status}'. "
            "Only pending_review or approved candidates can be promoted."
        )

    # Mark as approved
    candidate.status = "approved"
    candidate.reviewer = reviewer
    candidate.reviewed_at = datetime.now(UTC).isoformat()
    if review_notes:
        candidate.review_notes = review_notes

    # Update candidate file
    for f in candidates_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if data.get("id") == candidate.id:
                f.write_text(
                    json.dumps(
                        {
                            **data,
                            "status": candidate.status,
                            "reviewer": candidate.reviewer,
                            "reviewed_at": candidate.reviewed_at,
                            "review_notes": candidate.review_notes,
                        },
                        indent=2,
                    )
                    + "\n"
                )
                break
        except Exception:
            continue

    # Create golden task (simplified format - no model_responses as those are for replay)
    golden_dir.mkdir(parents=True, exist_ok=True)

    # Generate a clean filename
    filename = f"pr_{candidate.pr_number}_{candidate.task['agent']}.json"
    filepath = golden_dir / filename

    # Convert to golden task format
    golden_task = {
        "description": candidate.description,
        "task": candidate.task,
        "agent_spec": candidate.agent_spec,
        "expected_status": candidate.expected_status,
        "expected_gates": candidate.expected_gates,
        "model_responses": [
            # Placeholder - real model responses would come from actual execution
            {
                "content": [
                    {
                        "type": "text",
                        "text": f"Task from PR #{candidate.pr_number}. "
                        "This is a template golden task. "
                        "Execute it once to capture real model responses.",
                    }
                ],
                "stop_reason": "end_turn",
                "model": "placeholder",
                "input_tokens": 0,
                "output_tokens": 0,
            }
        ],
        "metadata": candidate.metadata,
    }

    filepath.write_text(json.dumps(golden_task, indent=2) + "\n")
    return filepath


def reject_candidate(
    candidate: CandidateGoldenTask,
    candidates_dir: Path,
    reviewer: str,
    reason: str,
) -> None:
    """Reject a candidate (mark as rejected, don't promote).

    Args:
        candidate: Candidate to reject
        candidates_dir: Directory where candidates are stored
        reviewer: Name of person rejecting
        reason: Reason for rejection
    """
    candidate.status = "rejected"
    candidate.reviewer = reviewer
    candidate.reviewed_at = datetime.now(UTC).isoformat()
    candidate.review_notes = reason

    # Update candidate file
    for f in candidates_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if data.get("id") == candidate.id:
                f.write_text(
                    json.dumps(
                        {
                            **data,
                            "status": candidate.status,
                            "reviewer": candidate.reviewer,
                            "reviewed_at": candidate.reviewed_at,
                            "review_notes": candidate.review_notes,
                        },
                        indent=2,
                    )
                    + "\n"
                )
                break
        except Exception:
            continue
