"""Live smoke tests requiring ANTHROPIC_API_KEY (marked live, deselected by default)."""

import pytest

from factory.harness.model import AnthropicModelClient, ModelRequest


@pytest.mark.live
def test_anthropic_client_smoke(model_config):
    """Make one real API call to verify credential setup."""
    client = AnthropicModelClient(model_config)
    tool = {
        "name": "echo",
        "description": "Echo back the input",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
    response = client.complete(
        ModelRequest(
            tier="frontier",
            system="You are a helpful assistant. Call the echo tool with 'ok'.",
            messages=[{"role": "user", "content": "Please echo 'ok'."}],
            tools=[tool],
        )
    )
    # Just verify we got a response with the expected structure
    assert response.model
    assert response.input_tokens > 0
    assert response.output_tokens > 0
    # Check we got tool calls (the agent should have called echo)
    tool_calls = response.tool_calls()
    assert len(tool_calls) > 0, "Expected at least one tool call"
    assert tool_calls[0]["name"] == "echo"
