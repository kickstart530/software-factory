"""Command line entry point: ``factory <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from factory.agents.spec import AgentCatalog
from factory.harness import (
    AgentHarness,
    AnthropicModelClient,
    FileCheckpointStore,
    HarnessDeps,
    JsonlTracer,
    ModelConfig,
    ToolContext,
)
from factory.schemas import EXPORTED_SCHEMAS, RiskTier, Task

REPO_ROOT = Path(__file__).resolve().parent.parent


def cmd_schemas_export(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, model in EXPORTED_SCHEMAS.items():
        (out / f"{name}.json").write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
    print(f"wrote {len(EXPORTED_SCHEMAS)} schemas to {out}")
    return 0


def cmd_agents_validate(args: argparse.Namespace) -> int:
    catalog = AgentCatalog(Path(args.agents))
    for spec in catalog:
        print(f"ok  {spec.name} v{spec.version}  tier={spec.model_tier}  gates={spec.gates}")
    return 0


def _harness(args: argparse.Namespace, task: Task) -> AgentHarness:
    catalog = AgentCatalog(Path(args.agents))
    spec = catalog[task.agent]
    state_dir = Path(args.state_dir)
    config = ModelConfig.load(Path(args.models))
    deps = HarnessDeps(
        model=AnthropicModelClient(config),
        model_config=config,
        checkpoints=FileCheckpointStore(state_dir / "checkpoints"),
        tracer=JsonlTracer(state_dir / "traces" / f"{task.id}.jsonl", task_id=task.id),
    )
    ctx = ToolContext(
        workspace=Path(args.workspace), artifacts_dir=state_dir / "artifacts" / task.id
    )
    return AgentHarness(
        spec, catalog.system_prompt(spec), deps, ctx, risk_tier=RiskTier(args.risk_tier)
    )


def cmd_run(args: argparse.Namespace) -> int:
    task = Task.model_validate_json(Path(args.task).read_text())
    result = _harness(args, task).run(task)
    print(result.model_dump_json(indent=2))
    return 0 if result.status == "succeeded" else 1


def cmd_answer(args: argparse.Namespace) -> int:
    task = Task.model_validate_json(Path(args.task).read_text())
    result = _harness(args, task).answer_escalation(task, args.answer)
    print(result.model_dump_json(indent=2))
    return 0 if result.status == "succeeded" else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory", description="Software factory harness CLI")
    p.add_argument("--agents", default=str(REPO_ROOT / "agents"), help="agent definitions dir")
    p.add_argument("--models", default=str(REPO_ROOT / "config" / "models.yaml"))
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("schemas", help="export JSON schemas")
    s.add_argument("action", choices=["export"])
    s.add_argument("--out", default=str(REPO_ROOT / "schemas"))
    s.set_defaults(func=cmd_schemas_export)

    a = sub.add_parser("agents", help="validate agent definitions")
    a.add_argument("action", choices=["validate"])
    a.set_defaults(func=cmd_agents_validate)

    for name, func, help_ in (
        ("run", cmd_run, "run one task with its agent"),
        ("answer", cmd_answer, "answer an escalation and resume the task"),
    ):
        r = sub.add_parser(name, help=help_)
        r.add_argument("--task", required=True, help="path to a Task JSON file")
        r.add_argument("--workspace", required=True, help="repository checkout to work in")
        r.add_argument(
            "--state-dir", default=".factory-runs", help="checkpoints, traces, artifacts"
        )
        r.add_argument("--risk-tier", default="low", choices=[t.value for t in RiskTier])
        if name == "answer":
            r.add_argument("--answer", required=True)
        r.set_defaults(func=func)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
