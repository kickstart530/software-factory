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


def test_api_key_read_from_env():
    import pytest

    from factory.harness.model import MissingCredentialsError, api_key_from_env

    assert api_key_from_env({"ANTHROPIC_API_KEY": " sk-ant-abc \n"}) == "sk-ant-abc"
    with pytest.raises(MissingCredentialsError, match="secrets.ANTHROPIC_API_KEY"):
        api_key_from_env({})
    with pytest.raises(MissingCredentialsError):
        api_key_from_env({"ANTHROPIC_API_KEY": "  "})


def test_client_built_from_env(model_config, monkeypatch):
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example")
    client = AnthropicModelClient(model_config)
    assert isinstance(client.client, anthropic.Anthropic)
    assert client.client.api_key == "sk-ant-test-key"
    assert str(client.client.base_url).startswith("https://gateway.example")


def test_cli_run_without_key_fails_clearly(tmp_path, monkeypatch, capsys):
    from factory.cli import main
    from tests.conftest import make_task

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    task = tmp_path / "task.json"
    task.write_text(make_task().model_dump_json())
    code = main(
        [
            "run",
            "--task",
            str(task),
            "--workspace",
            str(tmp_path),
            "--state-dir",
            str(tmp_path / "state"),
        ]
    )
    assert code == 2 and "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err
