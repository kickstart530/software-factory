"""Per-repository configuration overrides (Phase 5 continuous improvement).

Allows individual repositories to override agent budgets, gates, model tiers,
and conventions without forking the factory. The config lives in
``.factory/repo.yaml`` alongside ``gates.yaml``.

Precedence: repo override → global defaults (from agent definitions and config/).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from factory.schemas import Budget

ModelTier = Literal["frontier", "mid", "small"]


class AgentBudgetOverride(BaseModel):
    """Budget overrides for a specific agent in this repo."""

    model_config = ConfigDict(extra="forbid")

    tokens: int | None = Field(default=None, ge=1)
    usd: float | None = Field(default=None, ge=0)
    wall_clock_s: int | None = Field(default=None, ge=1)
    turns: int | None = Field(default=None, ge=1)
    tool_calls: int | None = Field(default=None, ge=1)


class AgentOverride(BaseModel):
    """Per-agent overrides for this repository."""

    model_config = ConfigDict(extra="forbid")

    model_tier: ModelTier | None = None
    budget: AgentBudgetOverride | None = None
    gates: list[str] | None = None
    max_gate_attempts: int | None = Field(default=None, ge=1)


class RepoConfig(BaseModel):
    """Repository-specific configuration overrides.

    Loaded from ``.factory/repo.yaml``. All fields are optional;
    missing fields use the agent's default from ``agents/*.yaml``.
    """

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(
        default=None, description="Human-readable description of this repo's overrides"
    )

    # Pointer to repo-specific conventions doc (relative to repo root)
    conventions: str | None = Field(
        default=None,
        description="Path to repo conventions (e.g., CONTRIBUTING.md, docs/AGENTS.md)",
    )

    # Per-agent overrides keyed by agent name
    agents: dict[str, AgentOverride] = Field(default_factory=dict)

    @classmethod
    def load(cls, workspace: Path) -> RepoConfig:
        """Load repo config from workspace/.factory/repo.yaml, or return empty if missing."""
        cfg = workspace / ".factory" / "repo.yaml"
        if not cfg.is_file():
            return cls()
        return cls.model_validate(yaml.safe_load(cfg.read_text()) or {})

    def apply_to_budget(self, agent_name: str, base_budget: Budget) -> Budget:
        """Apply repo-level budget overrides to an agent's base budget."""
        if agent_name not in self.agents or not self.agents[agent_name].budget:
            return base_budget

        overrides = self.agents[agent_name].budget
        return Budget(
            tokens=overrides.tokens if overrides.tokens is not None else base_budget.tokens,
            usd=overrides.usd if overrides.usd is not None else base_budget.usd,
            wall_clock_s=(
                overrides.wall_clock_s
                if overrides.wall_clock_s is not None
                else base_budget.wall_clock_s
            ),
            turns=overrides.turns if overrides.turns is not None else base_budget.turns,
            tool_calls=(
                overrides.tool_calls if overrides.tool_calls is not None else base_budget.tool_calls
            ),
            soft_limit_ratio=base_budget.soft_limit_ratio,  # Never overridden
        )

    def get_model_tier(self, agent_name: str, default_tier: ModelTier) -> ModelTier:
        """Get effective model tier for an agent, applying repo override if present."""
        if agent_name in self.agents and self.agents[agent_name].model_tier is not None:
            return self.agents[agent_name].model_tier
        return default_tier

    def get_gates(self, agent_name: str, default_gates: list[str]) -> list[str]:
        """Get effective gates for an agent, applying repo override if present."""
        if agent_name in self.agents and self.agents[agent_name].gates is not None:
            return self.agents[agent_name].gates
        return default_gates

    def get_max_gate_attempts(self, agent_name: str, default_max: int) -> int:
        """Get effective max gate attempts, applying repo override if present."""
        if agent_name in self.agents and self.agents[agent_name].max_gate_attempts is not None:
            return self.agents[agent_name].max_gate_attempts
        return default_max


class EffectiveAgentConfig(BaseModel):
    """The effective configuration after applying repo overrides."""

    model_config = ConfigDict(extra="forbid")

    agent_name: str
    model_tier: ModelTier
    budget: Budget
    gates: list[str]
    max_gate_attempts: int
    conventions: str | None = None
    source: str = Field(
        description=(
            "Where this config comes from: 'default' or 'repo-override' or 'partial-override'"
        )
    )
