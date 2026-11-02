"""Tests for P0 hardening fixes from architecture review.

Tests critical security and correctness edge cases:
1. Gate config protection from agent edits
2. Checkpoint keying by work_order_id + task_id
3. Task status stripping and Budget validation
4. secret_scan on full branch diff
5. Protected-path rules in run_command
6. Untrusted tool marking
"""

import subprocess
from pathlib import Path

import pytest

from factory.harness import FileCheckpointStore, GateRunner
from factory.harness.checkpoint import RunState
from factory.harness.policy import CallContext, RulePolicy
from factory.harness.tools import ToolContext, builtin_tools
from factory.schemas import Budget, RiskTier, Task, TaskStatus

TOOLS = {t.name: t for t in builtin_tools()}


# ==============================================================================
# Fix 1: Gate config must not be editable from workspace
# ==============================================================================


def test_gate_config_write_denied(tmp_path: Path):
    """Gate config (.factory/gates.yaml) cannot be edited by agents."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".factory").mkdir()
    (ws / ".factory" / "gates.yaml").write_text("gates:\n  test: {command: 'true'}\n")

    ctx = ToolContext(workspace=ws, artifacts_dir=tmp_path / "art")

    # Direct write to gate config should fail
    out = TOOLS["write_file"].invoke(
        ctx, {"path": ".factory/gates.yaml", "content": "gates:\n  hacked: {command: 'rm -rf /'}"}
    )
    assert out.is_error
    assert "protected" in out.content.lower()
    assert "gates.yaml" in out.content

    # Edit should also fail
    out = TOOLS["edit_file"].invoke(
        ctx, {"path": ".factory/gates.yaml", "old": "test", "new": "hacked"}
    )
    assert out.is_error
    assert "protected" in out.content.lower()


def test_gate_config_read_allowed(tmp_path: Path):
    """Reading gate config is allowed, only writes are blocked."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".factory").mkdir()
    content = "gates:\n  test: {command: 'true'}\n"
    (ws / ".factory" / "gates.yaml").write_text(content)

    ctx = ToolContext(workspace=ws, artifacts_dir=tmp_path / "art")
    out = TOOLS["read_file"].invoke(ctx, {"path": ".factory/gates.yaml"})
    assert not out.is_error
    assert "test" in out.content


# ==============================================================================
# Fix 2: Checkpoints keyed by work_order_id + task_id
# ==============================================================================


def test_checkpoint_keyed_by_work_order_and_task(tmp_path: Path):
    """Checkpoints are stored under work_order_id/task_id to avoid collisions."""
    store = FileCheckpointStore(tmp_path / "ckpt")

    state1 = RunState(
        task_id="T1",
        work_order_id="WO1",
        agent="implementer",
        agent_version=1,
        messages=[{"role": "user", "content": "task 1"}],
    )

    state2 = RunState(
        task_id="T1",  # Same task_id, different work_order_id
        work_order_id="WO2",
        agent="implementer",
        agent_version=1,
        messages=[{"role": "user", "content": "task 2"}],
    )

    ckpt1 = store.save(state1)
    ckpt2 = store.save(state2)

    # Both should save successfully
    assert ckpt1
    assert ckpt2

    # Load should return the correct checkpoint for each WO
    loaded1 = store.load("T1", "WO1")
    loaded2 = store.load("T1", "WO2")

    assert loaded1 is not None
    assert loaded2 is not None
    assert loaded1.work_order_id == "WO1"
    assert loaded2.work_order_id == "WO2"
    assert loaded1.messages[0]["content"] == "task 1"
    assert loaded2.messages[0]["content"] == "task 2"


def test_checkpoint_backward_compat_without_work_order(tmp_path: Path):
    """Loading checkpoints without work_order_id still works (backward compat)."""
    store = FileCheckpointStore(tmp_path / "ckpt")

    # Create old-style checkpoint directory structure (task_id only)
    old_dir = tmp_path / "ckpt" / "T1"
    old_dir.mkdir(parents=True)

    state = RunState(
        task_id="T1",
        work_order_id="WO1",
        agent="implementer",
        agent_version=1,
    )

    ckpt_file = old_dir / "12345.json"
    ckpt_file.write_text(state.model_dump_json())
    (old_dir / "LATEST").write_text("12345")

    # Should load from old path when work_order_id not provided
    loaded = store.load("T1")
    assert loaded is not None
    assert loaded.task_id == "T1"


# ==============================================================================
# Fix 3: Task status stripping and Budget validation
# ==============================================================================


def test_task_status_forced_to_pending():
    """Planner cannot set Task.status to succeeded."""
    # Try to create a task with SUCCEEDED status
    task = Task(
        id="T1",
        work_order_id="WO1",
        agent="implementer",
        title="test",
        instructions="test",
        status=TaskStatus.SUCCEEDED,  # This should be ignored
    )

    # Status should be forced to PENDING
    assert task.status == TaskStatus.PENDING


def test_empty_budget_rejected():
    """Empty budget {} is not allowed."""
    with pytest.raises(ValueError, match="empty budget"):
        Budget()


def test_budget_with_at_least_one_cap_allowed():
    """Budget with at least one cap is valid."""
    b = Budget(tokens=1000)
    assert b.tokens == 1000
    assert b.usd is None  # Others can still be None


# ==============================================================================
# Fix 4: secret_scan on full branch diff
# ==============================================================================


def test_secret_scan_checks_committed_changes(tmp_path: Path):
    """secret_scan checks full branch diff vs main, not just dirty files."""
    ws = tmp_path / "ws"
    ws.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws, check=True)

    # Create main branch with clean file
    (ws / "clean.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=ws, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=ws, check=True)

    # Create a feature branch with a committed secret
    subprocess.run(["git", "checkout", "-qb", "feature"], cwd=ws, check=True)
    secret_key = "AKIAIOSFODNN7EXAMPLE"
    (ws / "secret.py").write_text(f"api_key = '{secret_key}'\n")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "add secret"], cwd=ws, check=True)

    # Set up fake origin/main
    subprocess.run(["git", "branch", "origin/main", "main"], cwd=ws, check=True)

    # secret_scan should catch the committed secret
    runner = GateRunner()
    result = runner.run_one("secret_scan", ws)

    assert not result.passed
    assert "secret.py" in result.output_tail
    assert "aws-access-key" in result.output_tail


def test_secret_scan_checks_uncommitted_changes(tmp_path: Path):
    """secret_scan also checks uncommitted changes."""
    ws = tmp_path / "ws"
    ws.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws, check=True)

    (ws / "file.txt").write_text("clean\n")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=ws, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=ws, check=True)
    subprocess.run(["git", "branch", "origin/main"], cwd=ws, check=True)

    # Add uncommitted file with secret
    secret = "ghp_" + "x" * 36
    (ws / "uncommitted.py").write_text(f"token = '{secret}'\n")

    runner = GateRunner()
    result = runner.run_one("secret_scan", ws)

    assert not result.passed
    assert "uncommitted.py" in result.output_tail
    assert "uncommitted" in result.output_tail
    assert "github-token" in result.output_tail


# ==============================================================================
# Fix 5: Protected-path rules in run_command
# ==============================================================================


def test_protected_path_in_command_denied(tmp_path: Path):
    """Commands that touch protected paths are denied for low-risk tiers."""
    policy = RulePolicy()
    tool = TOOLS["run_command"]

    # Command touching .github/workflows should be denied for LOW risk
    ctx_low = CallContext(agent="implementer", risk_tier=RiskTier.LOW, tainted=False)

    decision = policy.check(tool, {"command": "echo 'x' > .github/workflows/ci.yml"}, ctx_low)
    assert not decision.allowed
    assert "protected path" in decision.reason.lower()

    # Same command allowed for HIGH risk
    ctx_high = CallContext(agent="implementer", risk_tier=RiskTier.HIGH, tainted=False)
    decision = policy.check(tool, {"command": "echo 'x' > .github/workflows/ci.yml"}, ctx_high)
    assert decision.allowed


def test_protected_path_patterns_in_various_commands():
    """Protected-path check works with various command patterns."""
    policy = RulePolicy()
    tool = TOOLS["run_command"]
    ctx = CallContext(agent="implementer", risk_tier=RiskTier.LOW, tainted=False)

    # Test various ways to touch protected paths
    blocked_commands = [
        "vim .github/workflows/deploy.yml",
        "sed -i 's/old/new/' .github/workflows/ci.yml",
        "edit Dockerfile",
        "cd infra && terraform apply",
        "mv old_migrations migrations/001_init.sql",
    ]

    for cmd in blocked_commands:
        decision = policy.check(tool, {"command": cmd}, ctx)
        assert not decision.allowed, f"Command should be blocked: {cmd}"
        assert "protected path" in decision.reason.lower()


def test_safe_commands_not_blocked_by_path_check():
    """Normal commands that don't touch protected paths are allowed."""
    policy = RulePolicy()
    tool = TOOLS["run_command"]
    ctx = CallContext(agent="implementer", risk_tier=RiskTier.LOW, tainted=False)

    safe_commands = [
        "pytest tests/",
        "python app.py",
        "git diff",
        "make build",
    ]

    for cmd in safe_commands:
        decision = policy.check(tool, {"command": cmd}, ctx)
        assert decision.allowed, f"Command should be allowed: {cmd}"


# ==============================================================================
# Fix 6: Untrusted tool marking documentation
# ==============================================================================


def test_builtin_tools_trust_levels():
    """Verify built-in tools have appropriate trust levels."""
    tools = {t.name: t for t in builtin_tools()}

    # File and code tools should be trusted (deterministic, workspace-only)
    for name in ["read_file", "write_file", "edit_file", "list_files", "search_code"]:
        assert tools[name].trust == "trusted"

    # run_command is sensitive but trusted (operates on workspace)
    assert tools["run_command"].trust == "trusted"
    assert tools["run_command"].sensitive is True

    # escalate is trusted (controlled by harness)
    assert tools["escalate"].trust == "trusted"


# ==============================================================================
# Integration test: harness respects all fixes
# ==============================================================================


def test_harness_enforces_gate_config_protection(tmp_path: Path):
    """Gate config cannot be modified via file tools."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".factory").mkdir()
    (ws / ".factory" / "gates.yaml").write_text("gates:\n  test: {command: 'true'}\n")

    ctx = ToolContext(workspace=ws, artifacts_dir=tmp_path / "art")

    # Try to overwrite gate config
    out = TOOLS["write_file"].invoke(ctx, {"path": ".factory/gates.yaml", "content": "hacked"})
    assert out.is_error
    assert "protected" in out.content.lower()

    # Original content should be unchanged
    assert "test" in (ws / ".factory" / "gates.yaml").read_text()


def test_harness_saves_checkpoints_with_work_order_id(tmp_path):
    """Verify checkpoint directory structure uses work_order_id."""
    from factory.harness.checkpoint import FileCheckpointStore, RunState

    store = FileCheckpointStore(tmp_path / "ckpt")

    state = RunState(
        task_id="T123",
        work_order_id="WO456",
        agent="implementer",
        agent_version=1,
        messages=[{"role": "user", "content": "test"}],
    )

    # Save should create path under work_order_id/task_id
    ckpt_id = store.save(state)
    assert ckpt_id

    # Verify directory structure
    ckpt_path = tmp_path / "ckpt" / "WO456" / "T123"
    assert ckpt_path.exists(), f"Checkpoint directory not at {ckpt_path}"
    assert (ckpt_path / "LATEST").exists()
    assert (ckpt_path / f"{ckpt_id}.json").exists()

    # Load with work_order_id should work
    loaded = store.load("T123", "WO456")
    assert loaded is not None
    assert loaded.work_order_id == "WO456"
    assert loaded.task_id == "T123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
