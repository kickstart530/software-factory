"""Integration tests for eval gate CLI."""

import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def repo_root():
    """Get the repository root."""
    return Path(__file__).parent.parent


def test_eval_gate_cli_help(repo_root):
    """Test that eval gate CLI help works."""
    result = subprocess.run(
        ["factory", "evals", "gate", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "baseline-agents" in result.stdout
    assert "--candidate-agents" in result.stdout
    assert "--json" in result.stdout


def test_eval_gate_cli_with_identical_configs(tmp_path, repo_root):
    """Test eval gate CLI with identical baseline and candidate."""
    workspace_root = tmp_path / "workspaces"
    artifacts_root = tmp_path / "artifacts"
    report_path = tmp_path / "report.json"

    result = subprocess.run(
        [
            "factory",
            "evals",
            "gate",
            "--baseline-agents",
            str(repo_root / "agents"),
            "--candidate-agents",
            str(repo_root / "agents"),
            "--workspace-root",
            str(workspace_root),
            "--artifacts-root",
            str(artifacts_root),
            "--json",
            str(report_path),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    # Should pass (exit 0) when configs are identical
    assert result.returncode == 0
    assert "GATE PASSED" in result.stdout
    assert "No regressions" in result.stdout

    # Check JSON report was created
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["has_regressions"] is False
    assert "baseline" in report
    assert "candidate" in report


def test_eval_gate_cli_output_format(tmp_path, repo_root):
    """Test that eval gate CLI output includes expected sections."""
    workspace_root = tmp_path / "workspaces"
    artifacts_root = tmp_path / "artifacts"

    result = subprocess.run(
        [
            "factory",
            "evals",
            "gate",
            "--baseline-agents",
            str(repo_root / "agents"),
            "--candidate-agents",
            str(repo_root / "agents"),
            "--workspace-root",
            str(workspace_root),
            "--artifacts-root",
            str(artifacts_root),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    # Check output format
    output = result.stdout
    assert "Eval Gate Report" in output
    assert "Baseline:" in output
    assert "Candidate:" in output
    assert "Passed:" in output
    assert "Failed:" in output
    assert "Pass rate:" in output
    assert "Total cost:" in output
