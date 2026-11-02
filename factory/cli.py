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
from factory.harness.approvals import FileBasedApprovalService
from factory.schemas import EXPORTED_SCHEMAS, ApprovalDecision, RiskTier, Task

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


def cmd_approve_list(args: argparse.Namespace) -> int:
    """List pending approval requests."""
    service = FileBasedApprovalService(Path(args.state_dir) / "approvals")
    pending = service.list_pending()
    if not pending:
        print("No pending approval requests.")
        return 0

    for req in pending:
        print(f"\n{'=' * 80}")
        print(f"ID:           {req.id}")
        print(f"Task:         {req.task_id}")
        print(f"Work Order:   {req.work_order_id}")
        print(f"Agent:        {req.agent}")
        print(f"Tool:         {req.tool_name}")
        print(f"Requested:    {req.requested_at}")
        print(f"Expires:      {req.expires_at}")
        print(f"\nReason:       {req.reason}")
        if req.risk_factors:
            print(f"Risk factors: {', '.join(req.risk_factors)}")
        print("\nTool args:")
        print(json.dumps(req.tool_args, indent=2))

    print(f"\n{'=' * 80}")
    print(f"\n{len(pending)} pending request(s)")
    print("\nTo review: factory approve show <request-id>")
    print("To approve: factory approve accept <request-id> --reason '...'")
    print("To deny: factory approve deny <request-id> --reason '...'")
    return 0


def cmd_approve_show(args: argparse.Namespace) -> int:
    """Show a specific approval request."""
    service = FileBasedApprovalService(Path(args.state_dir) / "approvals")
    req = service.get_request(args.request_id)
    if not req:
        print(f"Request {args.request_id} not found")
        return 1

    print(json.dumps(req.model_dump(), indent=2))
    return 0


def cmd_approve_accept(args: argparse.Namespace) -> int:
    """Approve a request."""
    service = FileBasedApprovalService(Path(args.state_dir) / "approvals")
    req = service.get_request(args.request_id)
    if not req:
        print(f"Request {args.request_id} not found")
        return 1

    if req.status != "pending":
        print(f"Request is {req.status}, cannot approve")
        return 1

    decision = ApprovalDecision(
        request_id=args.request_id,
        approved=True,
        reason=args.reason,
        decided_by=args.user or "cli-user",
    )
    service.record_decision(decision)
    print(f"✓ Approved request {args.request_id}")
    return 0


def cmd_approve_deny(args: argparse.Namespace) -> int:
    """Deny a request."""
    service = FileBasedApprovalService(Path(args.state_dir) / "approvals")
    req = service.get_request(args.request_id)
    if not req:
        print(f"Request {args.request_id} not found")
        return 1

    if req.status != "pending":
        print(f"Request is {req.status}, cannot deny")
        return 1

    decision = ApprovalDecision(
        request_id=args.request_id,
        approved=False,
        reason=args.reason,
        decided_by=args.user or "cli-user",
    )
    service.record_decision(decision)
    print(f"✗ Denied request {args.request_id}")
    return 0


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

    # Approval management commands
    approve = sub.add_parser("approve", help="manage approval requests")
    approve_sub = approve.add_subparsers(dest="approve_action", required=True)

    approve_list = approve_sub.add_parser("list", help="list pending approval requests")
    approve_list.add_argument("--state-dir", default=".factory-runs", help="state directory")
    approve_list.set_defaults(func=cmd_approve_list)

    approve_show = approve_sub.add_parser("show", help="show a specific approval request")
    approve_show.add_argument("request_id", help="approval request ID")
    approve_show.add_argument("--state-dir", default=".factory-runs", help="state directory")
    approve_show.set_defaults(func=cmd_approve_show)

    approve_accept = approve_sub.add_parser("accept", help="approve a request")
    approve_accept.add_argument("request_id", help="approval request ID")
    approve_accept.add_argument("--reason", required=True, help="reason for approval")
    approve_accept.add_argument("--user", help="user name (defaults to cli-user)")
    approve_accept.add_argument("--state-dir", default=".factory-runs", help="state directory")
    approve_accept.set_defaults(func=cmd_approve_accept)

    approve_deny = approve_sub.add_parser("deny", help="deny a request")
    approve_deny.add_argument("request_id", help="approval request ID")
    approve_deny.add_argument("--reason", required=True, help="reason for denial")
    approve_deny.add_argument("--user", help="user name (defaults to cli-user)")
    approve_deny.add_argument("--state-dir", default=".factory-runs", help="state directory")
    approve_deny.set_defaults(func=cmd_approve_deny)

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
