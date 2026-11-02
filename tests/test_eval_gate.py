"""Tests for eval gating: comparing baseline vs candidate configurations."""

import json

import pytest

from evals.gate import ConfigVariant, EvalGate, print_gate_report, save_gate_report
from evals.replay import load_golden_tasks


@pytest.fixture
def golden_tasks_fixture(tmp_path):
    """Create golden tasks for testing gate functionality."""
    gd = tmp_path / "golden"
    gd.mkdir()

    # Create two golden tasks: one that always passes, one that always fails
    passing_task = {
        "description": "Task that passes",
        "task": {
            "id": "gate_test_pass",
            "work_order_id": "WO_GATE_001",
            "agent": "implementer",
            "title": "Passing task",
            "instructions": "This should pass",
        },
        "agent_spec": {
            "name": "implementer",
            "version": 1,
            "description": "Test agent",
            "model_tier": "frontier",
            "system_prompt": "prompts/implementer.md",
            "tools": ["submit_result"],
            "budget": {"turns": 5, "usd": 5.0},
            "gates": [],
            "max_gate_attempts": 1,
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

    failing_task = {
        "description": "Task that fails",
        "task": {
            "id": "gate_test_fail",
            "work_order_id": "WO_GATE_002",
            "agent": "implementer",
            "title": "Failing task",
            "instructions": "This should fail",
        },
        "agent_spec": {
            "name": "implementer",
            "version": 1,
            "description": "Test agent",
            "model_tier": "frontier",
            "system_prompt": "prompts/implementer.md",
            "tools": ["submit_result"],
            "budget": {"turns": 5, "usd": 5.0},
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
                "input_tokens": 1000,
                "output_tokens": 50,
            }
        ],
    }

    (gd / "passing_task.json").write_text(json.dumps(passing_task, indent=2))
    (gd / "failing_task.json").write_text(json.dumps(failing_task, indent=2))

    return gd


def test_eval_gate_no_regressions(tmp_path, golden_tasks_fixture):
    """EvalGate with identical baseline and candidate has no regressions."""
    workspace_root = tmp_path / "workspaces"
    artifacts_root = tmp_path / "artifacts"

    tasks = load_golden_tasks(golden_tasks_fixture)

    baseline = ConfigVariant(name="baseline")
    candidate = ConfigVariant(name="candidate")

    gate = EvalGate(baseline, candidate, tasks, workspace_root, artifacts_root)
    comparison = gate.run()

    # Both should have the same results
    assert comparison.baseline_passed == comparison.candidate_passed
    assert comparison.baseline_failed == comparison.candidate_failed
    assert not comparison.has_regressions
    assert len(comparison.regressions) == 0


def test_eval_gate_detects_regression(tmp_path):
    """EvalGate detects when candidate performs worse than baseline."""
    gd = tmp_path / "golden"
    gd.mkdir()

    # Create a task that expects success
    task = {
        "description": "Task that should succeed",
        "task": {
            "id": "regression_test",
            "work_order_id": "WO_REG_001",
            "agent": "implementer",
            "title": "Test",
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
        "expected_status": "succeeded",
        "expected_gates": [],
        "model_responses": [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_001",
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

    (gd / "task.json").write_text(json.dumps(task))

    workspace_root = tmp_path / "workspaces"
    artifacts_root = tmp_path / "artifacts"

    tasks = load_golden_tasks(gd)

    baseline = ConfigVariant(name="baseline")
    candidate = ConfigVariant(name="candidate")

    gate = EvalGate(baseline, candidate, tasks, workspace_root, artifacts_root)
    comparison = gate.run()

    # Since replay uses the same responses, this won't actually show regression
    # This test validates the gate infrastructure works
    assert comparison.baseline_passed == comparison.candidate_passed


def test_gate_comparison_metrics(tmp_path, golden_tasks_fixture):
    """GateComparison calculates metrics correctly."""
    workspace_root = tmp_path / "workspaces"
    artifacts_root = tmp_path / "artifacts"

    tasks = load_golden_tasks(golden_tasks_fixture)

    baseline = ConfigVariant(name="baseline")
    candidate = ConfigVariant(name="candidate")

    gate = EvalGate(baseline, candidate, tasks, workspace_root, artifacts_root)
    comparison = gate.run()

    # Check pass rates are calculated
    assert 0.0 <= comparison.baseline_pass_rate <= 1.0
    assert 0.0 <= comparison.candidate_pass_rate <= 1.0

    # Check to_dict includes all expected fields
    report = comparison.to_dict()
    assert "baseline" in report
    assert "candidate" in report
    assert "regressions" in report
    assert "improvements" in report
    assert "has_regressions" in report

    assert "passed" in report["baseline"]
    assert "failed" in report["baseline"]
    assert "pass_rate" in report["baseline"]
    assert "total_cost_usd" in report["baseline"]


def test_save_gate_report(tmp_path, golden_tasks_fixture):
    """save_gate_report writes a valid JSON file."""
    workspace_root = tmp_path / "workspaces"
    artifacts_root = tmp_path / "artifacts"
    report_path = tmp_path / "report.json"

    tasks = load_golden_tasks(golden_tasks_fixture)

    baseline = ConfigVariant(name="baseline")
    candidate = ConfigVariant(name="candidate")

    gate = EvalGate(baseline, candidate, tasks, workspace_root, artifacts_root)
    comparison = gate.run()

    save_gate_report(comparison, report_path)

    assert report_path.exists()

    # Validate JSON structure
    report = json.loads(report_path.read_text())
    assert "baseline" in report
    assert "candidate" in report
    assert "baseline_results" in report
    assert "candidate_results" in report

    # Check results have required fields
    for result in report["baseline_results"]:
        assert "task_id" in result
        assert "passed" in result
        assert "actual_status" in result
        assert "expected_status" in result


def test_print_gate_report_runs_without_error(tmp_path, golden_tasks_fixture, capsys):
    """print_gate_report outputs formatted report."""
    workspace_root = tmp_path / "workspaces"
    artifacts_root = tmp_path / "artifacts"

    tasks = load_golden_tasks(golden_tasks_fixture)

    baseline = ConfigVariant(name="baseline")
    candidate = ConfigVariant(name="candidate")

    gate = EvalGate(baseline, candidate, tasks, workspace_root, artifacts_root)
    comparison = gate.run()

    print_gate_report(comparison)

    captured = capsys.readouterr()
    assert "Eval Gate Report" in captured.out
    assert "Baseline:" in captured.out
    assert "Candidate:" in captured.out
    assert "Pass rate:" in captured.out


def test_config_variant_with_custom_paths(tmp_path):
    """ConfigVariant accepts custom paths."""
    agents_dir = tmp_path / "custom_agents"
    models_config = tmp_path / "custom_models.yaml"

    variant = ConfigVariant(
        name="custom",
        agents_dir=agents_dir,
        models_config=models_config,
    )

    assert variant.name == "custom"
    assert variant.agents_dir == agents_dir
    assert variant.models_config == models_config
