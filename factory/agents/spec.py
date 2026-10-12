"""Declarative agent definitions: an agent is a configuration of the harness."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from factory.schemas import OUTPUT_SCHEMAS, Budget

ModelTier = Literal["frontier", "mid", "small"]


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: int = Field(ge=1)
    description: str
    model_tier: ModelTier
    system_prompt: str = Field(description="Path to the prompt file, relative to agents/.")
    tools: list[str] = Field(min_length=1)
    policy: str = Field(default="default", description="Named policy profile.")
    budget: Budget
    gates: list[str] = Field(default_factory=list)
    max_gate_attempts: int = Field(default=3, ge=1)
    output_schema: str
    sub_agents: list[str] = Field(default_factory=list)

    @field_validator("output_schema")
    @classmethod
    def _known_schema(cls, v: str) -> str:
        if v not in OUTPUT_SCHEMAS:
            raise ValueError(f"unknown output schema {v!r}; known: {sorted(OUTPUT_SCHEMAS)}")
        return v

    @field_validator("tools")
    @classmethod
    def _submit_required(cls, v: list[str]) -> list[str]:
        if "submit_result" not in v:
            raise ValueError("every agent needs the submit_result tool to finish")
        return v

    def output_model(self) -> type[BaseModel]:
        return OUTPUT_SCHEMAS[self.output_schema]


class AgentCatalog:
    """Loads and validates ``agents/*.yaml`` plus their prompt files."""

    def __init__(self, root: Path):
        self.root = root
        self._specs: dict[str, AgentSpec] = {}
        for path in sorted(root.glob("*.yaml")):
            spec = AgentSpec.model_validate(yaml.safe_load(path.read_text()))
            if spec.name != path.stem:
                raise ValueError(f"{path.name}: name {spec.name!r} must match the file name")
            if not (root / spec.system_prompt).is_file():
                raise ValueError(f"{path.name}: prompt {spec.system_prompt} not found")
            self._specs[spec.name] = spec
        for spec in self._specs.values():
            unknown = set(spec.sub_agents) - set(self._specs)
            if unknown:
                raise ValueError(f"{spec.name}: unknown sub_agents {sorted(unknown)}")

    def __getitem__(self, name: str) -> AgentSpec:
        return self._specs[name]

    def __iter__(self):
        return iter(self._specs.values())

    def names(self) -> list[str]:
        return sorted(self._specs)

    def system_prompt(self, spec: AgentSpec) -> str:
        return (self.root / spec.system_prompt).read_text()
