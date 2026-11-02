"""Command line entry point: ``factory <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evals import ReplayRunner, load_golden_tasks
from factory.agents.spec import AgentCatalog
from factory.control import KillSwitch
from factory.control.budget_ledger import FileBudgetLedger
from factory.control.cost_dashboard import CostDashboard
from factory.control.kill_switch import FileKillSwitchStore
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


def cmd_evals_replay(args: argparse.Namespace) -> int:
    golden_dir = Path(args.golden_dir)
    workspace_root = Path(args.workspace_root)
    artifacts_root = Path(args.artifacts_root)

    tasks = load_golden_tasks(golden_dir)
    if not tasks:
        print(f"No golden tasks found in {golden_dir}", file=sys.stderr)
        return 1

    runner = ReplayRunner(workspace_root, artifacts_root)
    results = runner.run_suite(tasks)

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)

    print(f"\n{'=' * 70}")
    print(f"Replay Results: {passed}/{len(results)} passed")
    print(f"{'=' * 70}\n")

    for result in results:
        status_mark = "✓" if result.passed else "✗"
        print(f"{status_mark} {result.task_id}: {result.description}")
        print(f"  Expected: {result.expected_status.value}, Got: {result.actual_status.value}")
        if result.error:
            print(f"  Error: {result.error}")
        if result.gates_passed:
            print(f"  Gates passed: {', '.join(result.gates_passed)}")
        if result.gates_failed:
            print(f"  Gates failed: {', '.join(result.gates_failed)}")
        print()

    if args.json:
        json_output = [
            {
                "task_id": r.task_id,
                "description": r.description,
                "passed": r.passed,
                "expected_status": r.expected_status.value,
                "actual_status": r.actual_status.value,
                "gates_passed": r.gates_passed,
                "gates_failed": r.gates_failed,
                "usage": r.usage,
                "error": r.error,
            }
            for r in results
        ]
        Path(args.json).write_text(json.dumps(json_output, indent=2))
        print(f"Wrote detailed results to {args.json}")

    return 0 if failed == 0 else 1


# --- Control commands -------------------------------------------------------


def cmd_control_pause(args: argparse.Namespace) -> int:
    """Pause the factory (global or per-tenant)."""
    store = FileKillSwitchStore(Path(args.state_dir) / "kill_switch")
    switch = KillSwitch(store)

    tenant = args.tenant if args.tenant != "global" else None
    status = switch.pause(tenant_id=tenant, reason=args.reason, paused_by=args.user or "cli-user")

    scope = f"tenant {tenant}" if tenant else "global"
    print(f"✓ Paused {scope}")
    print(f"  Reason: {status.reason}")
    print(f"  By: {status.paused_by}")
    print(f"  At: {status.paused_at}")
    return 0


def cmd_control_resume(args: argparse.Namespace) -> int:
    """Resume the factory (global or per-tenant)."""
    store = FileKillSwitchStore(Path(args.state_dir) / "kill_switch")
    switch = KillSwitch(store)

    tenant = args.tenant if args.tenant != "global" else None
    switch.resume(tenant_id=tenant)

    scope = f"tenant {tenant}" if tenant else "global"
    print(f"✓ Resumed {scope}")
    return 0


def cmd_control_status(args: argparse.Namespace) -> int:
    """Show kill switch status."""
    store = FileKillSwitchStore(Path(args.state_dir) / "kill_switch")
    switch = KillSwitch(store)

    if args.tenant and args.tenant != "global":
        status = switch.status(tenant_id=args.tenant)
        statuses = {args.tenant: status}
    else:
        statuses = switch.list_all()

    for name, status in statuses.items():
        print(f"\n{'=' * 70}")
        print(f"Kill Switch: {name}")
        print(f"{'=' * 70}")
        print(f"State: {status.state.value}")
        if status.is_paused():
            print(f"Paused at: {status.paused_at}")
            print(f"Paused by: {status.paused_by}")
            print(f"Reason: {status.reason}")

    return 0


def cmd_cost_report(args: argparse.Namespace) -> int:
    """Generate cost report."""
    ledger = FileBudgetLedger(Path(args.state_dir) / "budgets")
    dashboard = CostDashboard(ledger)

    period = args.period if hasattr(args, "period") and args.period else None

    if args.tenant:
        summary = dashboard.get_tenant_summary(args.tenant, period)
        print(dashboard.format_summary(summary))
    else:
        dashboard.print_report(period)

    return 0


def cmd_cost_export(args: argparse.Namespace) -> int:
    """Export cost metrics to JSONL."""
    ledger = FileBudgetLedger(Path(args.state_dir) / "budgets")
    dashboard = CostDashboard(ledger)

    period = args.period if args.period else None
    output = Path(args.output)

    count = dashboard.export_metrics_jsonl(output, period)
    print(f"✓ Exported {count} cost records to {output}")

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

    e = sub.add_parser("evals", help="run evaluation suite")
    e.add_argument("action", choices=["replay"], help="replay golden tasks")
    e.add_argument(
        "--golden-dir",
        default=str(REPO_ROOT / "evals" / "golden"),
        help="directory with golden task JSON files",
    )
    e.add_argument(
        "--workspace-root",
        default=".factory-evals/workspaces",
        help="root directory for test workspaces",
    )
    e.add_argument(
        "--artifacts-root",
        default=".factory-evals/artifacts",
        help="root directory for test artifacts",
    )
    e.add_argument("--json", help="write detailed results to JSON file")
    e.set_defaults(func=cmd_evals_replay)

    # Control commands
    control = sub.add_parser("control", help="factory control operations")
    control_sub = control.add_subparsers(dest="control_action", required=True)

    pause = control_sub.add_parser("pause", help="pause the factory")
    pause.add_argument(
        "--tenant",
        default="global",
        help="tenant to pause, or 'global' for factory-wide (default: global)",
    )
    pause.add_argument("--reason", required=True, help="reason for pause")
    pause.add_argument("--user", help="operator name (defaults to cli-user)")
    pause.add_argument("--state-dir", default=".factory-runs", help="state directory")
    pause.set_defaults(func=cmd_control_pause)

    resume = control_sub.add_parser("resume", help="resume the factory")
    resume.add_argument(
        "--tenant",
        default="global",
        help="tenant to resume, or 'global' for factory-wide (default: global)",
    )
    resume.add_argument("--state-dir", default=".factory-runs", help="state directory")
    resume.set_defaults(func=cmd_control_resume)

    status = control_sub.add_parser("status", help="show kill switch status")
    status.add_argument("--tenant", help="specific tenant to check, or omit for all")
    status.add_argument("--state-dir", default=".factory-runs", help="state directory")
    status.set_defaults(func=cmd_control_status)

    # Cost dashboard commands
    cost = sub.add_parser("cost", help="cost dashboard and reporting")
    cost_sub = cost.add_subparsers(dest="cost_action", required=True)

    report = cost_sub.add_parser("report", help="show cost report")
    report.add_argument("--tenant", help="specific tenant (default: all)")
    report.add_argument("--period", help="period (YYYY-MM or YYYY-MM-DD, default: current month)")
    report.add_argument("--state-dir", default=".factory-runs", help="state directory")
    report.set_defaults(func=cmd_cost_report)

    export = cost_sub.add_parser("export", help="export metrics to JSONL")
    export.add_argument("--output", required=True, help="output JSONL file path")
    export.add_argument("--period", help="period (YYYY-MM or YYYY-MM-DD, default: current month)")
    export.add_argument("--state-dir", default=".factory-runs", help="state directory")
    export.set_defaults(func=cmd_cost_export)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
