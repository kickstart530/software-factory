"""Model access behind a small interface (AD-5).

The harness talks to a ``ModelClient``; the cloud deployment points
``AnthropicModelClient`` at the Model Gateway via ``base_url``. Responses keep
the provider's content blocks verbatim, because the transcript must be
append-only: assistant turns, including thinking blocks, are sent back exactly
as received.

A/B Experiments (Phase 5): The ModelClient supports controlled A/B testing via
``ExperimentConfig``. Experiments route traffic to model/effort variants with
sticky assignment by work_order_id or tenant_id, and log the selected variant
to telemetry for scoring.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from factory.harness.budget import Price


class MissingCredentialsError(Exception):
    """Raised when required API credentials are missing or blank."""

    pass


class TierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    effort: str = "high"


class VariantConfig(BaseModel):
    """One variant in an A/B experiment."""

    model_config = ConfigDict(extra="forbid")
    weight: int = Field(ge=0)
    model: str
    effort: str = "high"


class ExperimentDefinition(BaseModel):
    """A/B experiment definition for a single tier."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool
    tier: str
    sticky_by: Literal["work_order_id", "tenant_id"] | None = None
    variants: dict[str, VariantConfig]

    @model_validator(mode="after")
    def _has_weight(self) -> ExperimentDefinition:
        if self.variants and sum(v.weight for v in self.variants.values()) <= 0:
            raise ValueError(f"experiment for tier {self.tier}: variant weights sum to 0")
        return self

    def select_variant(self, assignment_key: str | None) -> tuple[str, VariantConfig]:
        """Select a variant based on assignment key and weights.

        Returns (variant_name, variant_config). With an assignment key the choice
        is a deterministic hash of it; without one, weighted random.
        """
        if not self.variants:
            raise ValueError(f"experiment for tier {self.tier} has no variants")

        if assignment_key:
            # Deterministic sticky assignment via hash
            hash_input = f"{self.tier}:{assignment_key}".encode()
            hash_val = int(hashlib.sha256(hash_input).hexdigest(), 16)
            total_weight = sum(v.weight for v in self.variants.values())
            position = hash_val % total_weight

            cumulative = 0
            for name, variant in self.variants.items():
                cumulative += variant.weight
                if position < cumulative:
                    return name, variant

        # Weighted random selection (fallback or when no sticky assignment)
        import random

        total_weight = sum(v.weight for v in self.variants.values())
        position = random.randint(0, total_weight - 1)

        cumulative = 0
        for name, variant in self.variants.items():
            cumulative += variant.weight
            if position < cumulative:
                return name, variant

        # Fallback to first variant (should not reach here)
        first = next(iter(self.variants.items()))
        return first[0], first[1]


class ExperimentConfig(BaseModel):
    """Collection of A/B experiments."""

    model_config = ConfigDict(extra="forbid")
    experiments: dict[str, ExperimentDefinition] = {}

    @classmethod
    def load(cls, path: Path) -> ExperimentConfig:
        if not path.exists():
            return cls(experiments={})
        data = yaml.safe_load(path.read_text())
        if data is None or data.get("experiments") is None:
            return cls(experiments={})
        return cls.model_validate(data)

    def get_active_experiment(self, tier: str) -> tuple[str, ExperimentDefinition] | None:
        """Return (experiment_id, experiment) for the given tier, or None."""
        for exp_id, exp in self.experiments.items():
            if exp.enabled and exp.tier == tier:
                return exp_id, exp
        return None


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tiers: dict[str, TierConfig]
    pricing: dict[str, dict[str, float]] = {}

    @classmethod
    def load(cls, path: Path) -> ModelConfig:
        return cls.model_validate(yaml.safe_load(path.read_text()))

    def price(self, model: str) -> Price | None:
        p = self.pricing.get(model)
        return Price(**p) if p else None


@dataclass
class ModelRequest:
    tier: str
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    # Context for A/B experiment sticky assignment
    work_order_id: str | None = None
    tenant_id: str | None = None


@dataclass
class ModelResponse:
    content: list[dict[str, Any]]
    stop_reason: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    refusal_category: str | None = None
    # A/B experiment metadata (set if request was part of an experiment)
    experiment_id: str | None = None
    variant: str | None = None

    def tool_calls(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    def text(self) -> str:
        return "\n".join(b["text"] for b in self.content if b.get("type") == "text")


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...


class AnthropicModelClient:
    """Claude via the Messages API: streaming, adaptive thinking, refusal fallbacks.

    Supports A/B experiments (Phase 5): if an ExperimentConfig is provided and
    an active experiment exists for the requested tier, routes to a variant
    instead of the tier's default model/effort.
    """

    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(
        self,
        config: ModelConfig,
        client: Any | None = None,
        max_tokens: int = 64_000,
        experiments: ExperimentConfig | None = None,
    ):
        if client is None:
            import anthropic

            from factory.harness.credentials import load_api_key

            api_key = load_api_key()
            if not api_key:
                raise MissingCredentialsError(
                    "ANTHROPIC_API_KEY is required. In GitHub Actions, map it from "
                    "secrets: `env: ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}`"
                )
            base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
            # The SDK reads ANTHROPIC_BASE_URL from env, so unset it if blank
            if not base_url and "ANTHROPIC_BASE_URL" in os.environ:
                del os.environ["ANTHROPIC_BASE_URL"]
            client = anthropic.Anthropic(api_key=api_key)
        self.client = client
        self.config = config
        self.max_tokens = max_tokens
        self.experiments = experiments or ExperimentConfig()

    def complete(self, request: ModelRequest) -> ModelResponse:
        # Check for active A/B experiment for this tier
        experiment_id: str | None = None
        variant_name: str | None = None
        tier_config = self.config.tiers[request.tier]
        model = tier_config.model
        effort = tier_config.effort

        active = self.experiments.get_active_experiment(request.tier)
        if active:
            experiment_id, experiment = active
            # Determine sticky assignment key based on experiment config
            # One conversation must stay on one variant: thinking blocks and the
            # prompt cache are tied to the model, and switching mid-task mixes the
            # arms. So when the configured key is missing (or none is configured),
            # fall back to the Work Order; random only for one-off calls.
            assignment_key: str | None = None
            if experiment.sticky_by == "tenant_id":
                assignment_key = request.tenant_id
            assignment_key = assignment_key or request.work_order_id

            variant_name, variant = experiment.select_variant(assignment_key)
            model = variant.model
            effort = variant.effort

        tools = [{**t, "eager_input_streaming": True} for t in request.tools]
        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=self.max_tokens,
            system=[
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            messages=request.messages,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            # On a safety decline, re-run server-side on the recommended model
            # instead of failing the task.
            betas=[self.FALLBACK_BETA],
            fallbacks="default",
        )
        message = self._stream(kwargs)
        stop_details = getattr(message, "stop_details", None)
        usage = message.usage
        return ModelResponse(
            content=[b.model_dump(mode="json", exclude_none=True) for b in message.content],
            stop_reason=message.stop_reason or "end_turn",
            model=message.model,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            refusal_category=getattr(stop_details, "category", None) if stop_details else None,
            experiment_id=experiment_id,
            variant=variant_name,
        )

    def _stream(self, kwargs: dict[str, Any]):
        # With eager input streaming the server does not validate tool JSON; an
        # unparseable input raises ValueError from the stream. Re-issue once.
        for attempt in range(2):
            try:
                with self.client.beta.messages.stream(**kwargs) as stream:
                    return stream.get_final_message()
            except ValueError:
                if attempt == 1:
                    raise
        raise AssertionError("unreachable")


@dataclass
class ScriptedModelClient:
    """Deterministic client for tests and replays: returns pre-recorded responses."""

    responses: list[ModelResponse]
    requests: list[ModelRequest] = field(default_factory=list)

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(
            ModelRequest(
                request.tier,
                request.system,
                list(request.messages),
                request.tools,
                request.work_order_id,
                request.tenant_id,
            )
        )
        if not self.responses:
            raise RuntimeError("scripted model ran out of responses")
        return self.responses.pop(0)
