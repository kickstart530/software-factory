"""Integration tests for repo-config CLI commands."""

from pathlib import Path

from factory.cli import main


def test_repo_config_validate_missing(tmp_path: Path, capsys):
    """Validate with no .factory/repo.yaml reports defaults."""
    argv = ["repo-config", "validate", "--workspace", str(tmp_path)]
    exit_code = main(argv)

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "No .factory/repo.yaml found" in captured.out
    assert "using factory defaults" in captured.out


def test_repo_config_validate_valid(tmp_path: Path, capsys):
    """Validate a valid repo config."""
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()

    config_yaml = """
description: "Test configuration"
conventions: "CONTRIBUTING.md"
agents:
  implementer:
    model_tier: mid
    budget:
      tokens: 2000000
"""
    (factory_dir / "repo.yaml").write_text(config_yaml)

    argv = ["repo-config", "validate", "--workspace", str(tmp_path)]
    exit_code = main(argv)

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "✓ Valid repo config" in captured.out
    assert "Test configuration" in captured.out
    assert "CONTRIBUTING.md" in captured.out
    assert "implementer" in captured.out


def test_repo_config_validate_invalid(tmp_path: Path, capsys):
    """Validate reports error for invalid config."""
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()

    # Invalid YAML structure
    (factory_dir / "repo.yaml").write_text("agents: [invalid, list, structure]")

    argv = ["repo-config", "validate", "--workspace", str(tmp_path)]
    exit_code = main(argv)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "✗ Invalid repo config" in captured.err


def test_repo_config_show_default(tmp_path: Path, capsys):
    """Show effective config with no overrides."""
    # No .factory/repo.yaml, so should show defaults from agent spec
    argv = [
        "repo-config",
        "show",
        "--workspace",
        str(tmp_path),
        "--agent",
        "implementer",
    ]
    exit_code = main(argv)

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "implementer" in captured.out
    assert "Source:            default" in captured.out
    assert "Model tier:        frontier" in captured.out
    assert "tokens:" in captured.out
    assert "4,000,000" in captured.out


def test_repo_config_show_with_overrides(tmp_path: Path, capsys):
    """Show effective config with repo overrides applied."""
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()

    config_yaml = """
description: "Override test"
conventions: "docs/AGENTS.md"
agents:
  implementer:
    model_tier: mid
    budget:
      tokens: 2000000
      usd: 10.0
    gates: [build, unit_tests]
    max_gate_attempts: 2
"""
    (factory_dir / "repo.yaml").write_text(config_yaml)

    argv = [
        "repo-config",
        "show",
        "--workspace",
        str(tmp_path),
        "--agent",
        "implementer",
    ]
    exit_code = main(argv)

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Source:            repo-override" in captured.out
    assert "Model tier:        mid" in captured.out
    assert "2,000,000" in captured.out
    assert "$10.00" in captured.out
    assert "build, unit_tests" in captured.out
    assert "Max gate attempts: 2" in captured.out
    assert "Conventions:       docs/AGENTS.md" in captured.out

    # Should show the override diff
    assert "Overrides from .factory/repo.yaml:" in captured.out
    assert "frontier → mid" in captured.out
    assert "4,000,000 → 2,000,000" in captured.out


def test_repo_config_show_json(tmp_path: Path, capsys):
    """Show effective config as JSON."""
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()

    config_yaml = """
agents:
  implementer:
    model_tier: mid
"""
    (factory_dir / "repo.yaml").write_text(config_yaml)

    argv = [
        "repo-config",
        "show",
        "--workspace",
        str(tmp_path),
        "--agent",
        "implementer",
        "--json",
    ]
    exit_code = main(argv)

    assert exit_code == 0
    captured = capsys.readouterr()

    import json

    result = json.loads(captured.out)
    assert result["agent_name"] == "implementer"
    assert result["model_tier"] == "mid"
    assert result["source"] == "repo-override"
    assert "budget" in result


def test_repo_config_show_unknown_agent(tmp_path: Path, capsys):
    """Show fails gracefully for unknown agent."""
    argv = [
        "repo-config",
        "show",
        "--workspace",
        str(tmp_path),
        "--agent",
        "nonexistent",
    ]
    exit_code = main(argv)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.err
    assert "Available agents:" in captured.err


def test_repo_config_show_partial_override(tmp_path: Path, capsys):
    """Show correctly handles partial budget overrides."""
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()

    config_yaml = """
agents:
  implementer:
    budget:
      usd: 15.0  # only override cost, not tokens
"""
    (factory_dir / "repo.yaml").write_text(config_yaml)

    argv = [
        "repo-config",
        "show",
        "--workspace",
        str(tmp_path),
        "--agent",
        "implementer",
    ]
    exit_code = main(argv)

    assert exit_code == 0
    captured = capsys.readouterr()

    # Should show overridden cost
    assert "$15.00" in captured.out

    # Should show default tokens (4M)
    assert "4,000,000" in captured.out

    # Should show what was overridden
    assert "usd: $25.00 → $15.00" in captured.out
