"""AnthropicModelClient request shape, against a fake SDK client (no network)."""

from types import SimpleNamespace

import pytest

from factory.harness.model import AnthropicModelClient, MissingCredentialsError, ModelRequest


class _Block(SimpleNamespace):
    def model_dump(self, **_):
        return dict(vars(self))


class _Stream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.message


class FakeSDK:
    def __init__(self):
        self.calls = []
        msg = SimpleNamespace(
            content=[_Block(type="text", text="hi")],
            stop_reason="end_turn",
            stop_details=None,
            model="m",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=2,
                cache_read_input_tokens=5,
                cache_creation_input_tokens=0,
            ),
        )
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(stream=lambda **kw: (self.calls.append(kw), _Stream(msg))[1])
        )


def test_request_shape(model_config):
    sdk = FakeSDK()
    client = AnthropicModelClient(model_config, client=sdk)
    tool = {"name": "t", "description": "d", "input_schema": {"type": "object"}}
    resp = client.complete(
        ModelRequest("frontier", "sys", [{"role": "user", "content": "x"}], [tool])
    )
    kw = sdk.calls[0]
    assert kw["model"] == "test-model"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "low"}
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["tools"][0]["eager_input_streaming"] is True
    assert "eager_input_streaming" not in tool  # caller's definition untouched
    assert resp.text() == "hi" and resp.cache_read_tokens == 5


def test_missing_api_key(model_config, monkeypatch):
    """Missing ANTHROPIC_API_KEY raises MissingCredentialsError."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingCredentialsError) as exc_info:
        AnthropicModelClient(model_config)
    assert "ANTHROPIC_API_KEY is required" in str(exc_info.value)


def test_blank_api_key(model_config, monkeypatch):
    """Blank ANTHROPIC_API_KEY raises MissingCredentialsError."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    with pytest.raises(MissingCredentialsError) as exc_info:
        AnthropicModelClient(model_config)
    assert "ANTHROPIC_API_KEY is required" in str(exc_info.value)


def test_api_key_from_env(model_config, monkeypatch):
    """Client reads ANTHROPIC_API_KEY from the environment."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    client = AnthropicModelClient(model_config)
    assert client.client.api_key == "test-key-123"


def test_base_url_from_env(model_config, monkeypatch):
    """Client reads optional ANTHROPIC_BASE_URL from the environment."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.com")
    client = AnthropicModelClient(model_config)
    assert client.client.base_url == "https://gateway.example.com"


def test_blank_base_url_ignored(model_config, monkeypatch):
    """Blank ANTHROPIC_BASE_URL is treated as None."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "  ")
    client = AnthropicModelClient(model_config)
    # anthropic SDK's default base URL
    assert "anthropic.com" in str(client.client.base_url)
