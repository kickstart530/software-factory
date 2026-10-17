"""Model access behind a small interface (AD-5).

The harness talks to a ``ModelClient``; the cloud deployment points
``AnthropicModelClient`` at the Model Gateway via ``base_url``. Responses keep
the provider's content blocks verbatim, because the transcript must be
append-only: assistant turns, including thinking blocks, are sent back exactly
as received.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict

from factory.harness.budget import Price


class TierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    effort: str = "high"


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

    def tool_calls(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    def text(self) -> str:
        return "\n".join(b["text"] for b in self.content if b.get("type") == "text")


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...


API_KEY_ENV = "ANTHROPIC_API_KEY"
BASE_URL_ENV = "ANTHROPIC_BASE_URL"  # set to the Model Gateway in the cloud deployment


class MissingCredentialsError(RuntimeError):
    pass


def api_key_from_env(env: dict[str, str] | None = None) -> str:
    """The API key the harness uses: ``ANTHROPIC_API_KEY`` (a GitHub Actions secret in CI)."""
    key = (env if env is not None else os.environ).get(API_KEY_ENV, "").strip()
    if not key:
        raise MissingCredentialsError(
            f"{API_KEY_ENV} is not set. Export it locally, or in GitHub Actions map the "
            f"repository secret into the step: env: {API_KEY_ENV}: ${{{{ secrets.{API_KEY_ENV} }}}}"
        )
    return key


class AnthropicModelClient:
    """Claude via the Messages API: streaming, adaptive thinking, refusal fallbacks."""

    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, config: ModelConfig, client: Any | None = None, max_tokens: int = 64_000):
        if client is None:
            import anthropic

            client = anthropic.Anthropic(
                api_key=api_key_from_env(), base_url=os.environ.get(BASE_URL_ENV) or None
            )
        self.client = client
        self.config = config
        self.max_tokens = max_tokens

    def complete(self, request: ModelRequest) -> ModelResponse:
        tier = self.config.tiers[request.tier]
        tools = [{**t, "eager_input_streaming": True} for t in request.tools]
        kwargs: dict[str, Any] = dict(
            model=tier.model,
            max_tokens=self.max_tokens,
            system=[
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            messages=request.messages,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config={"effort": tier.effort},
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
            ModelRequest(request.tier, request.system, list(request.messages), request.tools)
        )
        if not self.responses:
            raise RuntimeError("scripted model ran out of responses")
        return self.responses.pop(0)
