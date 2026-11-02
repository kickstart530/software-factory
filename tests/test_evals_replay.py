"""Tests for the eval replay runner."""

import json
from pathlib import Path

import pytest

from evals.replay import ReplayRunner, load_golden_tasks
from factory.schemas import TaskStatus


@pytest.fixture
def golden_dir(tmp_path):
    """Create a temporary directory with golden tasks."""
    gd = tmp_path / "golden"
    gd.mkdir()
    
    task_data = {
        "description": "Simple test task",
        "task": {
            "id": "test_001",
            "work_order_id": "WO_001",
            "agent": "implementer",
            "title": "Test task",
            "instructions": "Do something simple",
        },
        "agent_spec": {
            "name": "implementer",
            "version": 1,
            "description": "Test agent",
            "model_tier": "frontier",
            "system_prompt": "prompts/implementer.md",
            "tools": ["read_file", "submit_result"],
            "budget": {"turns": 10, "usd": 5.0},
            "gates": [],
            "max_gate_attempts": 2,
            "output_schema": "ImplementationResult",
        },
        "expected_status": "succeeded",
        "expected_gates": [],
        "model_responses": [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_001",
                        "name": "submit_result",
                        "input": {"summary": "Done", "files_changed": []},
                    }
                ],
                "stop_reason": "tool_use",
                "model": "test-model",
                "input_tokens": 1000,
                "output_tokens": 50,
            }
        ],
    }
    
    (gd / "test_task.json").write_text(json.dumps(task_data, indent=2))
    return gd


def test_load_golden_tasks(golden_dir):
    """load_golden_tasks loads tasks from a directory."""
    tasks = load_golden_tasks(golden_dir)
    assert len(tasks) == 1
    assert tasks[0].task.id == "test_001"
    assert tasks[0].expected_status == TaskStatus.SUCCEEDED
    assert tasks[0].description == "Simple test task"


def test_load_golden_tasks_empty_dir(tmp_path):
    """load_golden_tasks returns empty list for empty directory."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    tasks = load_golden_tasks(empty_dir)
    assert tasks == []


def test_replay_runner_succeeds(tmp_path, golden_dir):
    """ReplayRunner runs a golden task and reports success."""
    workspace_root = tmp_path / "workspaces"
    artifacts_root = tmp_path / "artifacts"
    
    runner = ReplayRunner(workspace_root, artifacts_root)
    tasks = load_golden_tasks(golden_dir)
    
    result = runner.run(tasks[0])
    
    assert result.task_id == "test_001"
    assert result.passed
    assert result.actual_status == TaskStatus.SUCCEEDED
    assert result.expected_status == TaskStatus.SUCCEEDED


def test_replay_runner_suite(tmp_path, golden_dir):
    """ReplayRunner.run_suite runs multiple tasks."""
    workspace_root = tmp_path / "workspaces"
    artifacts_root = tmp_path / "artifacts"
    
    runner = ReplayRunner(workspace_root, artifacts_root)
    tasks = load_golden_tasks(golden_dir)
    
    results = runner.run_suite(tasks)
    
    assert len(results) == 1
    assert all(r.passed for r in results)


def test_replay_runner_detects_status_mismatch(tmp_path):
    """ReplayRunner detects when actual status doesn't match expected."""
    gd = tmp_path / "golden"
    gd.mkdir()
    
    task_data = {
        "description": "Task that should fail but succeeds",
        "task": {
            "id": "mismatch_001",
            "work_order_id": "WO_001",
            "agent": "implementer",
            "title": "Mismatch task",
            "instructions": "Test",
        },
        "agent_spec": {
            "name": "implementer",
            "version": 1,
            "description": "Test",
            "model_tier": "frontier",
            "system_prompt": "test",
            "tools": ["submit_result"],
            "budget": {"turns": 5},
            "gates": [],
            "max_gate_attempts": 1,
            "output_schema": "ImplementationResult",
        },
        "expected_status": "failed",
        "expected_gates": [],
        "model_responses": [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_002",
                        "name": "submit_result",
                        "input": {"summary": "Done"},
                    }
                ],
                "stop_reason": "tool_use",
                "model": "test-model",
                "input_tokens": 100,
                "output_tokens": 20,
            }
        ],
    }
    
    (gd / "mismatch.json").write_text(json.dumps(task_data))
    
    workspace_root = tmp_path / "workspaces"
    artifacts_root = tmp_path / "artifacts"
    runner = ReplayRunner(workspace_root, artifacts_root)
    
    tasks = load_golden_tasks(gd)
    result = runner.run(tasks[0])
    
    assert not result.passed
    assert result.actual_status == TaskStatus.SUCCEEDED
    assert result.expected_status == TaskStatus.FAILED


def test_load_real_golden_tasks():
    """Load the actual golden tasks from evals/golden."""
    repo_root = Path(__file__).parent.parent
    golden_dir = repo_root / "evals" / "golden"
    
    if not golden_dir.exists():
        pytest.skip("Golden tasks directory does not exist")
    
    tasks = load_golden_tasks(golden_dir)
    
    assert len(tasks) >= 5
    
    task_ids = {t.task.id for t in tasks}
    assert "golden_001" in task_ids
    assert "golden_002" in task_ids
    assert "golden_003" in task_ids
    
    statuses = {t.expected_status for t in tasks}
    assert TaskStatus.SUCCEEDED in statuses
    assert TaskStatus.FAILED in statuses or TaskStatus.ESCALATED in statuses
