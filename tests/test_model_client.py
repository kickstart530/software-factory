"""AnthropicModelClient request shape, against a fake SDK client (no network)."""

from types import SimpleNamespace

from factory.harness.model import AnthropicModelClient, ModelRequest


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
