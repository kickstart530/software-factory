"""Live smoke test against the real API. Deselected by default; run with ``pytest -m live``.

In GitHub Actions the key comes from the ANTHROPIC_API_KEY repository secret.
"""

import os

import pytest

from factory.harness.model import AnthropicModelClient, ModelConfig, ModelRequest
from tests.conftest import REPO_ROOT

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY unset"),
]


def test_live_tool_call_round_trip():
    config = ModelConfig.load(REPO_ROOT / "config" / "models.yaml")
    client = AnthropicModelClient(config, max_tokens=4096)
    tool = {
        "name": "submit_result",
        "description": "Submit the final answer.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    }
    resp = client.complete(
        ModelRequest(
            tier="small",
            system="You are a test harness. Always finish by calling submit_result.",
            messages=[{"role": "user", "content": "Call submit_result with summary 'ok'."}],
            tools=[tool],
        )
    )
    assert resp.stop_reason == "tool_use", resp
    call = resp.tool_calls()[0]
    assert call["name"] == "submit_result" and call["input"]["summary"]
    assert resp.input_tokens > 0 and resp.output_tokens > 0
