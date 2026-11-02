"""Tests for Temporal client integration (github_app/temporal_client.py)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from github_app.temporal_client import submit_work_order_to_temporal


@pytest.mark.asyncio
async def test_submit_work_order_success():
    """Test successful Work Order submission to Temporal."""
    # Create mock client
    mock_client = MagicMock()
    mock_handle = MagicMock()
    mock_handle.first_execution_run_id = "test-run-123"
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    work_order = {
        "id": "WO-test-123",
        "title": "Test work order",
        "description": "Test description",
        "repo": {
            "url": "https://github.com/test/repo.git",
            "base_branch": "main",
        },
        "acceptance_criteria": [
            {
                "id": "builds",
                "description": "Code builds",
                "verification": "gate",
                "gate": "build",
            }
        ],
        "risk_tier": "low",
        "requested_by": "testuser",
        "labels": ["github-issue-123"],
    }

    result = await submit_work_order_to_temporal(mock_client, work_order, task_queue="test-queue")

    assert result["status"] == "submitted"
    assert result["work_order_id"] == "WO-test-123"
    assert result["workflow_id"] == "factory-WO-test-123"
    assert result["workflow_run_id"] == "test-run-123"

    # Verify workflow started correctly
    mock_client.start_workflow.assert_called_once()
    call_args = mock_client.start_workflow.call_args

    # The DAG is passed via args keyword parameter
    assert call_args.kwargs["id"] == "factory-WO-test-123"
    assert call_args.kwargs["task_queue"] == "test-queue"

    # Workflow name is the first positional arg, DAG is in args kwarg
    assert len(call_args.args) >= 1
    assert call_args.args[0] == "execute_task_dag"

    # Verify TaskDAG structure - it's in the args kwarg, not positional
    task_dag = call_args.kwargs["args"][0]
    assert task_dag["work_order_id"] == "WO-test-123"
    assert len(task_dag["tasks"]) == 1
    task = task_dag["tasks"][0]
    assert task["id"] == "impl-1"
    assert task["agent"] == "implementer"
    assert task["title"] == "Test work order"
    assert task["status"] == "pending"


@pytest.mark.asyncio
async def test_submit_work_order_temporal_error():
    """Test Work Order submission when Temporal fails."""
    mock_client = MagicMock()
    mock_client.start_workflow = AsyncMock(side_effect=Exception("Temporal connection failed"))

    work_order = {
        "id": "WO-test-456",
        "title": "Test",
        "description": "Test",
        "repo": {"url": "https://github.com/test/repo.git", "base_branch": "main"},
        "acceptance_criteria": [],
        "risk_tier": "low",
        "requested_by": "testuser",
        "labels": [],
    }

    with pytest.raises(Exception, match="Temporal connection failed"):
        await submit_work_order_to_temporal(mock_client, work_order)


@pytest.mark.asyncio
async def test_submit_work_order_creates_minimal_dag():
    """Test that Work Order is converted to a minimal single-task DAG."""
    mock_client = MagicMock()
    mock_handle = MagicMock()
    mock_handle.first_execution_run_id = "run-id"
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    work_order = {
        "id": "WO-minimal",
        "title": "Minimal test",
        "description": "Minimal description",
        "repo": {"url": "https://github.com/test/repo.git", "base_branch": "main"},
        "acceptance_criteria": [
            {"id": "test", "description": "Test", "verification": "gate", "gate": "test"}
        ],
        "risk_tier": "low",
        "requested_by": "user",
        "labels": [],
    }

    await submit_work_order_to_temporal(mock_client, work_order)

    # Extract the DAG that was passed to start_workflow
    call_args = mock_client.start_workflow.call_args
    task_dag = call_args.kwargs["args"][0]

    # Verify it's a minimal DAG with one task
    assert task_dag["work_order_id"] == "WO-minimal"
    assert len(task_dag["tasks"]) == 1

    task = task_dag["tasks"][0]
    assert task["work_order_id"] == "WO-minimal"
    assert task["agent"] == "implementer"
    assert task["title"] == "Minimal test"
    assert task["instructions"] == "Minimal description"
    assert task["depends_on"] == []
    assert task["branch"] is None
    assert task["status"] == "pending"
    assert task["acceptance_criteria"] == work_order["acceptance_criteria"]
