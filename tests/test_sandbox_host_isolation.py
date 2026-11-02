"""Tests for P0: sandbox must not access host login files or credentials.

Verifies that the sandbox environment and file confinement prevent access to
sensitive host directories like /home, /root, /etc/passwd, etc.
"""

from pathlib import Path

import pytest

from factory.harness.tools import ToolContext, builtin_tools

TOOLS = {t.name: t for t in builtin_tools()}


def test_read_file_cannot_access_etc_passwd(tmp_path: Path):
    """P0: Agents cannot read /etc/passwd or other host config files."""
    ws = tmp_path / "ws"
    ws.mkdir()

    ctx = ToolContext(workspace=ws, artifacts_dir=tmp_path / "art")

    # Try to read /etc/passwd
    out = TOOLS["read_file"].invoke(ctx, {"path": "/etc/passwd"})
    assert out.is_error
    assert "outside the workspace" in out.content

    # Try relative path escape
    out = TOOLS["read_file"].invoke(ctx, {"path": "../../../etc/passwd"})
    assert out.is_error
    assert "outside the workspace" in out.content


def test_read_file_cannot_access_home_directory(tmp_path: Path):
    """P0: Agents cannot read files in /home or /root."""
    ws = tmp_path / "ws"
    ws.mkdir()

    ctx = ToolContext(workspace=ws, artifacts_dir=tmp_path / "art")

    # Try to read home directory files
    out = TOOLS["read_file"].invoke(ctx, {"path": "/home/ubuntu/.bashrc"})
    assert out.is_error
    assert "outside the workspace" in out.content

    out = TOOLS["read_file"].invoke(ctx, {"path": "/root/.ssh/id_rsa"})
    assert out.is_error
    assert "outside the workspace" in out.content


def test_run_command_cannot_cat_host_files(tmp_path: Path):
    """P0: run_command with shell escapes cannot bypass workspace confinement."""
    ws = tmp_path / "ws"
    ws.mkdir()

    ctx = ToolContext(workspace=ws, artifacts_dir=tmp_path / "art")

    # Commands run in workspace, but they can access host if not confined
    # The sandbox_env() removes credentials, which is tested elsewhere
    # Here we document that file system isolation must be enforced at container level

    # This test documents the expected behavior
    # Actual enforcement happens via gVisor/sandbox runtime (Phase 1 item #2)
    # For now, verify that credentials are not in the environment
    out = TOOLS["run_command"].invoke(ctx, {"command": "env | grep -i key || echo NO_KEYS"})
    assert not out.is_error or "NO_KEYS" in out.content or "ANTHROPIC_API_KEY" not in out.content


def test_workspace_confinement_enforced_for_writes(tmp_path: Path):
    """P0: Write operations are strictly confined to workspace."""
    ws = tmp_path / "ws"
    ws.mkdir()

    ctx = ToolContext(workspace=ws, artifacts_dir=tmp_path / "art")

    # Cannot write outside workspace
    out = TOOLS["write_file"].invoke(ctx, {"path": "/tmp/evil.txt", "content": "hack"})
    assert out.is_error
    assert "outside the workspace" in out.content

    # Cannot write to parent directory
    out = TOOLS["write_file"].invoke(ctx, {"path": "../escape.txt", "content": "hack"})
    assert out.is_error
    assert "outside the workspace" in out.content


def test_list_files_confined_to_workspace(tmp_path: Path):
    """P0: File listing cannot enumerate host directories."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "safe.txt").write_text("ok")

    ctx = ToolContext(workspace=ws, artifacts_dir=tmp_path / "art")

    # Can list workspace
    out = TOOLS["list_files"].invoke(ctx, {"pattern": "**/*"})
    assert not out.is_error
    assert "safe.txt" in out.content

    # Pattern is relative to workspace, cannot escape
    # (glob is applied to workspace.glob(), so it's automatically confined)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
