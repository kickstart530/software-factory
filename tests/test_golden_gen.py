"""Tests for golden task generation from PR metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.golden_gen import (
    PRMetadata,
    generate_candidate_task,
    parse_pr_diff,
    promote_candidate,
    reject_candidate,
    save_candidate,
)
from factory.golden_gen.pr_parser import (
    estimate_complexity,
    extract_instructions,
    infer_agent_type,
)


@pytest.fixture
def fixtures_dir():
    """Return path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def pr_simple_fix(fixtures_dir):
    """Load simple bug fix PR fixture."""
    data = json.loads((fixtures_dir / "pr_simple_fix.json").read_text())
    return PRMetadata(**data)


@pytest.fixture
def pr_complex_feature(fixtures_dir):
    """Load complex feature PR fixture."""
    data = json.loads((fixtures_dir / "pr_complex_feature.json").read_text())
    return PRMetadata(**data)


@pytest.fixture
def pr_docs_update(fixtures_dir):
    """Load docs update PR fixture."""
    data = json.loads((fixtures_dir / "pr_docs_update.json").read_text())
    return PRMetadata(**data)


class TestPRParser:
    """Tests for PR parsing functionality."""

    def test_parse_pr_diff_single_file(self, pr_simple_fix):
        """Test parsing diff with single file."""
        file_diffs = parse_pr_diff(pr_simple_fix.diff)
        assert "validators.py" in file_diffs
        assert "tests/test_validators.py" in file_diffs
        assert len(file_diffs) == 2

    def test_parse_pr_diff_multiple_files(self, pr_complex_feature):
        """Test parsing diff with multiple files."""
        file_diffs = parse_pr_diff(pr_complex_feature.diff)
        assert "middleware/auth.py" in file_diffs

    def test_infer_agent_type_implementer(self, pr_simple_fix):
        """Test inferring implementer agent type."""
        agent_type = infer_agent_type(pr_simple_fix)
        assert agent_type == "implementer"

    def test_infer_agent_type_docs(self, pr_docs_update):
        """Test inferring docs agent type."""
        agent_type = infer_agent_type(pr_docs_update)
        assert agent_type == "docs"

    def test_extract_instructions(self, pr_simple_fix):
        """Test extracting task instructions."""
        instructions = extract_instructions(pr_simple_fix)
        assert "Fix email validation regex" in instructions
        assert "validators.py" in instructions

    def test_estimate_complexity_low(self, pr_simple_fix):
        """Test complexity estimation for simple PR."""
        complexity = estimate_complexity(pr_simple_fix)
        assert complexity["complexity"] == "low"
        assert complexity["files_count"] == 2
        assert complexity["total_changes"] == 23
        assert complexity["suggested_budget"]["turns"] == 20

    def test_estimate_complexity_medium(self, pr_complex_feature):
        """Test complexity estimation for complex feature PR."""
        complexity = estimate_complexity(pr_complex_feature)
        # 347 additions + 12 deletions = 359 total changes (>200) -> high
        assert complexity["complexity"] == "high"
        assert complexity["files_count"] == 6

    def test_estimate_complexity_high(self):
        """Test complexity estimation for high PR."""
        pr = PRMetadata(
            number=999,
            title="Major refactor",
            description="Large refactoring",
            author="dev@example.com",
            merged_at="2026-11-01T00:00:00Z",
            files_changed=[f"file{i}.py" for i in range(20)],
            additions=1500,
            deletions=800,
            diff="",
        )
        complexity = estimate_complexity(pr)
        assert complexity["complexity"] == "high"
        assert complexity["suggested_budget"]["turns"] == 80


class TestTaskGenerator:
    """Tests for golden task generation."""

    def test_generate_candidate_task(self, pr_simple_fix):
        """Test generating candidate from PR metadata."""
        candidate = generate_candidate_task(pr_simple_fix)

        assert candidate.pr_number == 123
        assert candidate.task["agent"] == "implementer"
        assert "Fix email validation regex" in candidate.task["title"]
        assert candidate.expected_status == "succeeded"
        assert candidate.status == "pending_review"

    def test_generate_candidate_with_custom_agent(self, pr_simple_fix):
        """Test generating candidate with custom agent type."""
        candidate = generate_candidate_task(pr_simple_fix, custom_agent="reviewer")

        assert candidate.task["agent"] == "reviewer"

    def test_candidate_has_metadata(self, pr_simple_fix):
        """Test that candidate includes PR metadata."""
        candidate = generate_candidate_task(pr_simple_fix)

        assert "source_pr" in candidate.metadata
        assert candidate.metadata["source_pr"]["number"] == 123
        assert candidate.metadata["source_pr"]["author"] == "developer@example.com"
        assert "complexity" in candidate.metadata
        assert "files_changed" in candidate.metadata

    def test_save_and_load_candidate(self, pr_simple_fix, tmp_path):
        """Test saving and loading candidate."""
        candidate = generate_candidate_task(pr_simple_fix)

        # Save
        candidates_dir = tmp_path / "candidates"
        filepath = save_candidate(candidate, candidates_dir)
        assert filepath.exists()

        # Load
        from factory.golden_gen.task_generator import load_candidate

        loaded = load_candidate(filepath)
        assert loaded.id == candidate.id
        assert loaded.pr_number == candidate.pr_number
        assert loaded.task == candidate.task

    def test_promote_candidate(self, pr_simple_fix, tmp_path):
        """Test promoting candidate to golden set."""
        candidate = generate_candidate_task(pr_simple_fix)

        candidates_dir = tmp_path / "candidates"
        golden_dir = tmp_path / "golden"

        # Save candidate
        save_candidate(candidate, candidates_dir)

        # Promote
        golden_path = promote_candidate(
            candidate,
            golden_dir,
            candidates_dir,
            reviewer="test-reviewer",
            review_notes="Good test case",
        )

        assert golden_path.exists()
        assert candidate.status == "approved"
        assert candidate.reviewer == "test-reviewer"

        # Verify golden task structure
        golden_task = json.loads(golden_path.read_text())
        assert "description" in golden_task
        assert "task" in golden_task
        assert "agent_spec" in golden_task
        assert "expected_status" in golden_task
        assert "model_responses" in golden_task

    def test_promote_candidate_rejected(self, pr_simple_fix, tmp_path):
        """Test that rejected candidates cannot be promoted."""
        candidate = generate_candidate_task(pr_simple_fix)
        candidate.status = "rejected"

        candidates_dir = tmp_path / "candidates"
        golden_dir = tmp_path / "golden"

        save_candidate(candidate, candidates_dir)

        with pytest.raises(ValueError, match="Cannot promote"):
            promote_candidate(
                candidate,
                golden_dir,
                candidates_dir,
                reviewer="test-reviewer",
            )

    def test_reject_candidate(self, pr_simple_fix, tmp_path):
        """Test rejecting a candidate."""
        candidate = generate_candidate_task(pr_simple_fix)

        candidates_dir = tmp_path / "candidates"
        save_candidate(candidate, candidates_dir)

        reject_candidate(
            candidate,
            candidates_dir,
            reviewer="test-reviewer",
            reason="Too trivial",
        )

        assert candidate.status == "rejected"
        assert candidate.reviewer == "test-reviewer"
        assert candidate.review_notes == "Too trivial"


class TestCLIIntegration:
    """Integration tests for CLI commands."""

    def test_generate_from_fixture(self, fixtures_dir, tmp_path):
        """Test generating candidate via CLI."""
        from factory.cli import main

        candidates_dir = tmp_path / "candidates"
        pr_fixture = fixtures_dir / "pr_simple_fix.json"

        result = main(
            [
                "golden",
                "generate",
                str(pr_fixture),
                "--candidates-dir",
                str(candidates_dir),
            ]
        )

        assert result == 0
        assert len(list(candidates_dir.glob("*.json"))) == 1

    def test_list_candidates(self, fixtures_dir, tmp_path):
        """Test listing candidates via CLI."""
        from factory.cli import main

        candidates_dir = tmp_path / "candidates"

        # Generate a candidate first
        main(
            [
                "golden",
                "generate",
                str(fixtures_dir / "pr_simple_fix.json"),
                "--candidates-dir",
                str(candidates_dir),
            ]
        )

        # List candidates
        result = main(
            [
                "golden",
                "list",
                "--candidates-dir",
                str(candidates_dir),
            ]
        )

        assert result == 0

    def test_promote_via_cli(self, fixtures_dir, tmp_path):
        """Test promoting candidate via CLI."""
        from factory.cli import main

        candidates_dir = tmp_path / "candidates"
        golden_dir = tmp_path / "golden"

        # Generate candidate
        main(
            [
                "golden",
                "generate",
                str(fixtures_dir / "pr_simple_fix.json"),
                "--candidates-dir",
                str(candidates_dir),
            ]
        )

        # Get candidate ID
        candidate_file = list(candidates_dir.glob("*.json"))[0]
        data = json.loads(candidate_file.read_text())
        candidate_id = data["id"]

        # Promote
        result = main(
            [
                "golden",
                "promote",
                candidate_id,
                "--reviewer",
                "test-reviewer",
                "--candidates-dir",
                str(candidates_dir),
                "--golden-dir",
                str(golden_dir),
            ]
        )

        assert result == 0
        assert len(list(golden_dir.glob("*.json"))) == 1

    def test_reject_via_cli(self, fixtures_dir, tmp_path):
        """Test rejecting candidate via CLI."""
        from factory.cli import main

        candidates_dir = tmp_path / "candidates"

        # Generate candidate
        main(
            [
                "golden",
                "generate",
                str(fixtures_dir / "pr_simple_fix.json"),
                "--candidates-dir",
                str(candidates_dir),
            ]
        )

        # Get candidate ID
        candidate_file = list(candidates_dir.glob("*.json"))[0]
        data = json.loads(candidate_file.read_text())
        candidate_id = data["id"]

        # Reject
        result = main(
            [
                "golden",
                "reject",
                candidate_id,
                "--reviewer",
                "test-reviewer",
                "--reason",
                "Not suitable",
                "--candidates-dir",
                str(candidates_dir),
            ]
        )

        assert result == 0

        # Verify rejection
        data = json.loads(candidate_file.read_text())
        assert data["status"] == "rejected"
        assert data["review_notes"] == "Not suitable"
