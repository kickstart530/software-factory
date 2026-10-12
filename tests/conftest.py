from __future__ import annotations

import itertools
import subprocess
from pathlib import Path

import pytest

from factory.agents.spec import AgentSpec
from factory.harness import (
    AgentHarness,
    FileCheckpointStore,
    GateCommand,
    GateRunner,
    HarnessDeps,
    MemoryTracer,
    ModelConfig,
    ModelResponse,
    ScriptedModelClient,
    ToolContext,
)
from factory.schemas import Budget, RiskTier, Task

REPO_ROOT = Path(__file__).resolve().parent.parent
_ids = itertools.count()


def tool_call(name: str, **input) -> dict:
    return {"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name, "input": input}


def reply(*blocks: dict, stop_reason: str | None = None, text: str | None = None) -> ModelResponse:
    content = ([{"type": "text", "text": text}] if text else []) + list(blocks)
    if stop_reason is None:
        stop_reason = "tool_use" if blocks else "end_turn"
    return ModelResponse(
        content=content,
        stop_reason=stop_reason,
        model="test-model",
        input_tokens=1000,
        output_tokens=100,
    )


def submit(**output) -> dict:
    output.setdefault("summary", "done")
    return tool_call("submit_result", **output)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("def add(a, b):\n    return a - b\n")
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=ws,
        check=True,
    )
    return ws


@pytest.fixture
def model_config() -> ModelConfig:
    return ModelConfig.model_validate(
        {
            "tiers": {
                t: {"model": "test-model", "effort": "low"} for t in ("frontier", "mid", "small")
            },
            "pricing": {"test-model": {"input": 1.0, "output": 5.0}},
        }
    )


def make_spec(**overrides) -> AgentSpec:
    data = dict(
        name="implementer",
        version=1,
        description="test",
        model_tier="frontier",
        system_prompt="prompts/implementer.md",
        tools=["read_file", "write_file", "edit_file", "run_command", "escalate", "submit_result"],
        budget=Budget(turns=20),
        gates=["unit_tests"],
        max_gate_attempts=2,
        output_schema="ImplementationResult",
    )
    data.update(overrides)
    return AgentSpec.model_validate(data)


def make_task(**overrides) -> Task:
    data = dict(
        id="T1",
        work_order_id="WO1",
        agent="implementer",
        title="Fix add",
        instructions="add() subtracts; make it add.",
    )
    data.update(overrides)
    return Task.model_validate(data)


@pytest.fixture
def build(tmp_path, workspace, model_config):
    """Build a harness around a scripted model. Returns (harness, model, tracer)."""

    def _build(
        responses, spec=None, gates=None, risk_tier=RiskTier.LOW, checkpoints=None, tools=None
    ):
        model = ScriptedModelClient(list(responses))
        tracer = MemoryTracer()
        deps = HarnessDeps(
            model=model,
            model_config=model_config,
            checkpoints=checkpoints or FileCheckpointStore(tmp_path / "ckpt"),
            tracer=tracer,
            tools=tools,
        )
        ctx = ToolContext(workspace=workspace, artifacts_dir=tmp_path / "artifacts")
        gate_runner = (
            gates
            if gates is not None
            else GateRunner(
                {
                    "unit_tests": GateCommand(
                        command="python -B -c 'import app; assert app.add(2, 3) == 5'"
                    )
                }
            )
        )
        h = AgentHarness(spec or make_spec(), "test prompt", deps, ctx, risk_tier, gate_runner)
        return h, model, tracer

    return _build
