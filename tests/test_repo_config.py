"""Tests for per-repository configuration overrides (Phase 5)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from factory.repo_config import AgentBudgetOverride, AgentOverride, RepoConfig
from factory.schemas import Budget


def test_empty_repo_config():
    """With no .factory/repo.yaml, load returns an empty config."""
    cfg = RepoConfig()
    assert cfg.description is None
    assert cfg.conventions is None
    assert cfg.agents == {}


def test_load_missing_file(tmp_path: Path):
    """Loading from a workspace without .factory/repo.yaml returns empty config."""
    cfg = RepoConfig.load(tmp_path)
    assert cfg.description is None
    assert cfg.agents == {}


def test_load_valid_repo_config(tmp_path: Path):
    """Load and parse a valid repo config."""
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()

    config_yaml = """
description: "Test repo overrides"
conventions: "docs/AGENTS.md"
agents:
  implementer:
    model_tier: mid
    budget:
      tokens: 2000000
      usd: 10.0
    gates: [build, unit_tests]
    max_gate_attempts: 2
  reviewer:
    model_tier: frontier
"""
    (factory_dir / "repo.yaml").write_text(config_yaml)

    cfg = RepoConfig.load(tmp_path)
    assert cfg.description == "Test repo overrides"
    assert cfg.conventions == "docs/AGENTS.md"
    assert "implementer" in cfg.agents
    assert "reviewer" in cfg.agents

    impl = cfg.agents["implementer"]
    assert impl.model_tier == "mid"
    assert impl.budget is not None
    assert impl.budget.tokens == 2000000
    assert impl.budget.usd == 10.0
    assert impl.gates == ["build", "unit_tests"]
    assert impl.max_gate_attempts == 2

    rev = cfg.agents["reviewer"]
    assert rev.model_tier == "frontier"
    assert rev.budget is None


def test_apply_budget_overrides():
    """Budget overrides are applied correctly."""
    base = Budget(
        tokens=4000000,
        usd=25.0,
        wall_clock_s=2700,
        turns=150,
        tool_calls=400,
    )

    cfg = RepoConfig(
        agents={
            "implementer": AgentOverride(
                budget=AgentBudgetOverride(
                    tokens=2000000,
                    usd=12.5,
                    # wall_clock_s, turns, tool_calls not overridden
                )
            )
        }
    )

    effective = cfg.apply_to_budget("implementer", base)
    assert effective.tokens == 2000000
    assert effective.usd == 12.5
    assert effective.wall_clock_s == 2700  # from base
    assert effective.turns == 150  # from base
    assert effective.tool_calls == 400  # from base
    assert effective.soft_limit_ratio == base.soft_limit_ratio


def test_apply_budget_no_override():
    """When no override exists, base budget is returned unchanged."""
    base = Budget(tokens=4000000, usd=25.0)
    cfg = RepoConfig()

    effective = cfg.apply_to_budget("implementer", base)
    assert effective == base


def test_get_model_tier_override():
    """Model tier override is applied."""
    cfg = RepoConfig(agents={"implementer": AgentOverride(model_tier="mid")})

    assert cfg.get_model_tier("implementer", "frontier") == "mid"
    assert cfg.get_model_tier("reviewer", "frontier") == "frontier"


def test_get_gates_override():
    """Gates override is applied."""
    cfg = RepoConfig(agents={"implementer": AgentOverride(gates=["build", "lint"])})

    default_gates = ["build", "unit_tests", "lint", "secret_scan"]
    assert cfg.get_gates("implementer", default_gates) == ["build", "lint"]
    assert cfg.get_gates("reviewer", default_gates) == default_gates


def test_get_max_gate_attempts_override():
    """Max gate attempts override is applied."""
    cfg = RepoConfig(agents={"implementer": AgentOverride(max_gate_attempts=5)})

    assert cfg.get_max_gate_attempts("implementer", 3) == 5
    assert cfg.get_max_gate_attempts("reviewer", 3) == 3


def test_invalid_model_tier():
    """Invalid model tier is rejected."""
    with pytest.raises(ValidationError):
        AgentOverride(model_tier="invalid")


def test_invalid_budget_values():
    """Negative or zero budget values are rejected."""
    with pytest.raises(ValidationError):
        AgentBudgetOverride(tokens=-100)

    with pytest.raises(ValidationError):
        AgentBudgetOverride(tokens=0)

    with pytest.raises(ValidationError):
        AgentBudgetOverride(usd=-10.0)


def test_extra_fields_forbidden():
    """Extra fields in repo config are rejected."""
    with pytest.raises(ValidationError):
        RepoConfig(unknown_field="value")

    with pytest.raises(ValidationError):
        AgentOverride(unknown_field="value")

    with pytest.raises(ValidationError):
        AgentBudgetOverride(unknown_field="value")


def test_partial_budget_override():
    """Can override only some budget fields."""
    base = Budget(
        tokens=4000000,
        usd=25.0,
        wall_clock_s=2700,
        turns=150,
        tool_calls=400,
    )

    cfg = RepoConfig(
        agents={
            "implementer": AgentOverride(
                budget=AgentBudgetOverride(usd=10.0)  # only override cost
            )
        }
    )

    effective = cfg.apply_to_budget("implementer", base)
    assert effective.tokens == 4000000
    assert effective.usd == 10.0
    assert effective.wall_clock_s == 2700
    assert effective.turns == 150
    assert effective.tool_calls == 400


def test_repo_config_round_trip(tmp_path: Path):
    """Config can be saved and loaded."""
    factory_dir = tmp_path / ".factory"
    factory_dir.mkdir()

    original = RepoConfig(
        description="Round-trip test",
        conventions="CONTRIBUTING.md",
        agents={
            "implementer": AgentOverride(
                model_tier="mid",
                budget=AgentBudgetOverride(tokens=1000000, usd=5.0),
                gates=["build"],
                max_gate_attempts=1,
            )
        },
    )

    # Manual save since we don't have a save() method (write-only use case)
    import yaml

    (factory_dir / "repo.yaml").write_text(yaml.safe_dump(original.model_dump(exclude_none=True)))

    loaded = RepoConfig.load(tmp_path)
    assert loaded.description == original.description
    assert loaded.conventions == original.conventions
    assert "implementer" in loaded.agents
    assert loaded.agents["implementer"].model_tier == "mid"
