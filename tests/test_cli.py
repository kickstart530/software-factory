"""CLI error handling and exit codes."""

import json

import pytest

from factory.cli import main


@pytest.fixture
def task_json(tmp_path):
    """Create a minimal task JSON file."""
    task = {
        "id": "T1",
        "work_order_id": "WO1",
        "agent": "implementer",
        "title": "Test task",
        "instructions": "Test instructions",
    }
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task))
    return task_path


def test_run_missing_api_key(task_json, tmp_path, monkeypatch):
    """factory run exits with code 2 when ANTHROPIC_API_KEY is missing."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    exit_code = main(
        [
            "run",
            "--task",
            str(task_json),
            "--workspace",
            str(workspace),
            "--state-dir",
            str(tmp_path / "state"),
        ]
    )
    assert exit_code == 2


def test_answer_missing_api_key(task_json, tmp_path, monkeypatch):
    """factory answer exits with code 2 when ANTHROPIC_API_KEY is missing."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    exit_code = main(
        [
            "answer",
            "--task",
            str(task_json),
            "--workspace",
            str(workspace),
            "--state-dir",
            str(tmp_path / "state"),
            "--answer",
            "yes",
        ]
    )
    assert exit_code == 2
