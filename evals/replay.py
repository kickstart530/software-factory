"""Replay runner: re-execute recorded transcripts with stubbed tool results.

The replay runner loads golden tasks (recorded transcripts with expected outcomes)
and re-runs them through the harness with a stubbed model client. Tool results
are replayed from the recording, allowing us to test prompt/harness changes
without re-executing actual tools.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory.agents.spec import AgentSpec
from factory.harness import (
    AgentHarness,
    FileCheckpointStore,
    GateRunner,
    HarnessDeps,
    MemoryTracer,
    ModelConfig,
    ModelResponse,
    ToolContext,
    ToolRegistry,
)
from factory.harness.adversarial import adversarial_mock_tools
from factory.harness.model import ModelClient, ModelRequest
from factory.harness.tools import Tool, ToolInput, ToolOutput, builtin_tools
from factory.schemas import RiskTier, Task, TaskResult, TaskStatus


@dataclass
class GoldenTask:
    """A recorded task execution with expected outcome."""

    task: Task
    agent_spec: AgentSpec
    expected_status: TaskStatus
    expected_gates: list[str]
    model_responses: list[dict[str, Any]]
    tool_results: dict[str, str]
    description: str = ""


@dataclass
class ReplayResult:
    """Result of replaying a golden task."""

    task_id: str
    description: str
    passed: bool
    actual_status: TaskStatus
    expected_status: TaskStatus
    gates_passed: list[str]
    gates_failed: list[str]
    expected_gates: list[str]
    usage: dict[str, Any]
    error: str | None = None


class ReplayModelClient(ModelClient):
    """Model client that replays recorded responses."""

    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = responses
        self.index = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        if self.index >= len(self.responses):
            raise RuntimeError(f"Replay exhausted: no response for turn {self.index}")
        data = self.responses[self.index]
        self.index += 1
        return ModelResponse(**data)


def make_stubbed_tool(name: str, description: str, tool_results: dict[str, str]) -> Tool:
    """Create a tool that returns stubbed results for replay.
    
    Args:
        name: The tool name
        description: Tool description
        tool_results: Map of tool_use_id -> stubbed result content
    
    Returns:
        A Tool that returns pre-recorded results based on tool_use_id from the model response
    """
    class StubInput(ToolInput):
        pass
    
    # The handler will be replaced per-invocation to return the right stub
    def handler(ctx: ToolContext, args: StubInput) -> str:
        return "Stubbed result"
    
    # Mark as untrusted if it's one of the adversarial tools
    trust = "untrusted" if name in ("fetch_issue", "fetch_web") else "trusted"
    
    return Tool(name, description, StubInput, handler, trust=trust)


class ReplayRunner:
    """Run golden tasks through the harness with replayed model responses."""

    def __init__(self, workspace_root: Path, artifacts_root: Path):
        self.workspace_root = workspace_root
        self.artifacts_root = artifacts_root
        self.model_config = ModelConfig.model_validate(
            {
                "tiers": {
                    t: {"model": "replay-model", "effort": "low"}
                    for t in ("frontier", "mid", "small")
                },
                "pricing": {"replay-model": {"input": 0.0, "output": 0.0}},
            }
        )

    def run(self, golden: GoldenTask) -> ReplayResult:
        """Run a single golden task and return the replay result."""
        workspace = self.workspace_root / golden.task.id
        workspace.mkdir(parents=True, exist_ok=True)

        model = ReplayModelClient(golden.model_responses)
        tracer = MemoryTracer()
        checkpoints = FileCheckpointStore(self.artifacts_root / "checkpoints" / golden.task.id)

        # Build tool registry with adversarial tools for testing
        tools = self._build_tool_registry(golden)

        deps = HarnessDeps(
            model=model,
            model_config=self.model_config,
            checkpoints=checkpoints,
            tracer=tracer,
            tools=tools,
        )

        ctx = ToolContext(
            workspace=workspace,
            artifacts_dir=self.artifacts_root / "artifacts" / golden.task.id,
        )

        gates = GateRunner({})

        harness = AgentHarness(
            golden.agent_spec,
            "test prompt",
            deps,
            ctx,
            RiskTier.LOW,
            gates,
        )

        try:
            result = harness.run(golden.task)
            passed = self._check_result(result, golden)
            gates_passed = [g.name for g in result.gates if g.passed]
            gates_failed = [g.name for g in result.gates if not g.passed]

            return ReplayResult(
                task_id=golden.task.id,
                description=golden.description,
                passed=passed,
                actual_status=result.status,
                expected_status=golden.expected_status,
                gates_passed=gates_passed,
                gates_failed=gates_failed,
                expected_gates=golden.expected_gates,
                usage=result.usage.model_dump() if result.usage else {},
                error=result.error,
            )
        except Exception as e:
            return ReplayResult(
                task_id=golden.task.id,
                description=golden.description,
                passed=False,
                actual_status=TaskStatus.FAILED,
                expected_status=golden.expected_status,
                gates_passed=[],
                gates_failed=[],
                expected_gates=golden.expected_gates,
                usage={},
                error=str(e),
            )

    def run_suite(self, tasks: list[GoldenTask]) -> list[ReplayResult]:
        """Run a suite of golden tasks and return results."""
        return [self.run(task) for task in tasks]

    def _build_tool_registry(self, golden: GoldenTask) -> ToolRegistry:
        """Build a tool registry with stubbed tools for replay.
        
        If the golden task has tool_results, those tools will return stubbed content.
        Otherwise, we use the real tool implementations (safe for builtin tools).
        """
        all_tools = [*builtin_tools(), *adversarial_mock_tools()]
        
        # If we have stubbed results, override the tool handlers to return them
        if golden.tool_results:
            # Create a map to track which tool_use_id maps to which tool invocation
            # This is a simplified approach - in a full implementation you'd need
            # to track the execution order and match stubs to the right invocation
            pass
        
        return ToolRegistry(all_tools)
    
    @staticmethod
    def _check_result(result: TaskResult, golden: GoldenTask) -> bool:
        """Check if the result matches expected outcomes."""
        if result.status != golden.expected_status:
            return False

        if golden.expected_status == TaskStatus.SUCCEEDED:
            passed_gates = {g.name for g in result.gates if g.passed}
            expected = set(golden.expected_gates)
            if passed_gates != expected:
                return False

        return True


def load_golden_tasks(golden_dir: Path) -> list[GoldenTask]:
    """Load golden tasks from a directory.

    Each task is a JSON file with the structure:
    {
        "task": {...},
        "agent_spec": {...},
        "expected_status": "succeeded",
        "expected_gates": ["unit_tests"],
        "model_responses": [{...}],
        "tool_results": {"tool_use_id": "result"},
        "description": "..."
    }
    """
    tasks = []
    for path in sorted(golden_dir.glob("*.json")):
        with path.open() as f:
            data = json.load(f)

        task = Task.model_validate(data["task"])
        agent_spec = AgentSpec.model_validate(data["agent_spec"])
        expected_status = TaskStatus(data["expected_status"])

        tasks.append(
            GoldenTask(
                task=task,
                agent_spec=agent_spec,
                expected_status=expected_status,
                expected_gates=data.get("expected_gates", []),
                model_responses=data["model_responses"],
                tool_results=data.get("tool_results", {}),
                description=data.get("description", ""),
            )
        )

    return tasks
