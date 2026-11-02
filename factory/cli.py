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
    MissingCredentialsError,
    ModelConfig,
    ToolContext,
)
from factory.harness.approvals import FileBasedApprovalService
from factory.memory import (
    FileBackedLessonStore,
    LessonProposal,
    LessonQuery,
    LessonService,
    LessonStatus,
    PromotionDecision,
)
from factory.release import CanaryOrchestrator, CanaryPlan, CanaryRun, FakeBackend
from factory.schemas import EXPORTED_SCHEMAS, ApprovalDecision, RiskTier, Task
from factory.secrets import MintRequest, SecretStatus
from factory.secrets.backends import EnvFileBackend

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
    try:
        result = _harness(args, task).run(task)
        print(result.model_dump_json(indent=2))
        return 0 if result.status == "succeeded" else 1
    except MissingCredentialsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def cmd_answer(args: argparse.Namespace) -> int:
    task = Task.model_validate_json(Path(args.task).read_text())
    try:
        result = _harness(args, task).answer_escalation(task, args.answer)
        print(result.model_dump_json(indent=2))
        return 0 if result.status == "succeeded" else 1
    except MissingCredentialsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


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


def cmd_secrets_mint(args: argparse.Namespace) -> int:
    """Mint a new task-scoped credential."""
    state_dir = Path(args.state_dir) / "secrets"
    backend = EnvFileBackend(state_dir)

    request = MintRequest(
        task_id=args.task_id,
        work_order_id=args.work_order_id,
        repo_url=args.repo_url,
        branch=args.branch,
        tool_name=args.tool_name,
        scopes=args.scopes or [],
        ttl_seconds=args.ttl,
        reason=args.reason or "Manual mint via CLI",
    )

    try:
        credential = backend.mint(request)
        print(f"✓ Minted credential: {credential.credential_id}")
        print(f"  Task: {credential.task_id}")
        print(f"  Tool: {credential.tool_name}")
        print(f"  Expires: {credential.expires_at}")
        print(f"  Fingerprint: {credential.fingerprint}")
        print("\nNOTE: Secret value injected into environment, never displayed.")
        return 0
    except Exception as e:
        print(f"✗ Failed to mint credential: {e}", file=sys.stderr)
        return 1


def cmd_secrets_list(args: argparse.Namespace) -> int:
    """List credentials, optionally filtered by task or status."""
    state_dir = Path(args.state_dir) / "secrets"
    backend = EnvFileBackend(state_dir)

    status_filter = SecretStatus(args.status) if args.status else None
    credentials = backend.list_credentials(
        task_id=args.task_id,
        status=status_filter,
    )

    if not credentials:
        print("No credentials found matching filters.")
        return 0

    print(f"Found {len(credentials)} credential(s):\n")
    for cred in credentials:
        print(f"ID:      {cred.credential_id}")
        print(f"Task:    {cred.task_id}")
        print(f"Tool:    {cred.tool_name}")
        print(f"Status:  {cred.status.value}")
        print(f"Minted:  {cred.minted_at}")
        print(f"Expires: {cred.expires_at}")
        if cred.revoked_at:
            print(f"Revoked: {cred.revoked_at}")
        print(f"Fingerprint: {cred.fingerprint}")
        print()

    return 0


def cmd_secrets_status(args: argparse.Namespace) -> int:
    """Show status of a specific credential."""
    state_dir = Path(args.state_dir) / "secrets"
    backend = EnvFileBackend(state_dir)

    credential = backend.get_credential(args.credential_id)
    if not credential:
        print(f"Credential {args.credential_id} not found", file=sys.stderr)
        return 1

    print(json.dumps(credential.model_dump(mode="json"), indent=2))
    return 0


def cmd_secrets_revoke(args: argparse.Namespace) -> int:
    """Revoke a credential before its natural expiry."""
    state_dir = Path(args.state_dir) / "secrets"
    backend = EnvFileBackend(state_dir)

    if backend.revoke(args.credential_id):
        print(f"✓ Revoked credential {args.credential_id}")
        return 0
    else:
        print(f"✗ Could not revoke credential {args.credential_id}", file=sys.stderr)
        return 1


def cmd_secrets_cleanup(args: argparse.Namespace) -> int:
    """Clean up expired credentials."""
    state_dir = Path(args.state_dir) / "secrets"
    backend = EnvFileBackend(state_dir)

    count = backend.cleanup_expired()
    print(f"Cleaned up {count} expired credential(s)")
    return 0


def cmd_memory_propose(args: argparse.Namespace) -> int:
    """Propose a new lesson."""
    store = FileBackedLessonStore(Path(args.state_dir) / "memory")
    service = LessonService(store)

    proposal = LessonProposal(
        text=args.text,
        repo=args.repo,
        tags=args.tags if args.tags else [],
        run_id=args.run_id,
        work_order_id=args.work_order_id,
        ttl_days=args.ttl_days,
    )

    lesson = service.propose_lesson(proposal)
    print(f"✓ Proposed lesson {lesson.id}")
    print(json.dumps(lesson.model_dump(), indent=2))
    return 0


def cmd_memory_list(args: argparse.Namespace) -> int:
    """List lessons by status."""
    store = FileBackedLessonStore(Path(args.state_dir) / "memory")
    service = LessonService(store)

    status_map = {
        "candidates": service.list_candidates,
        "promoted": service.list_promoted,
        "rejected": service.list_rejected,
        "expired": service.list_expired,
    }

    lessons = status_map[args.status]()

    if not lessons:
        print(f"No {args.status} lessons.")
        return 0

    for lesson in lessons:
        print(f"\n{'=' * 80}")
        print(f"ID:         {lesson.id}")
        print(f"Status:     {lesson.status.value}")
        print(f"Created:    {lesson.created_at}")
        if lesson.repo:
            print(f"Repo:       {lesson.repo}")
        if lesson.tags:
            print(f"Tags:       {', '.join(lesson.tags)}")
        if lesson.run_id:
            print(f"Run:        {lesson.run_id}")
        if lesson.work_order_id:
            print(f"Work Order: {lesson.work_order_id}")
        if lesson.reviewed_by:
            print(f"Reviewed:   {lesson.reviewed_at} by {lesson.reviewed_by}")
            print(f"Reason:     {lesson.review_reason}")
        print(f"\nText:\n{lesson.text}")

    print(f"\n{'=' * 80}")
    print(f"\n{len(lessons)} {args.status} lesson(s)")
    return 0


def cmd_memory_promote(args: argparse.Namespace) -> int:
    """Promote a candidate lesson."""
    store = FileBackedLessonStore(Path(args.state_dir) / "memory")
    service = LessonService(store)

    decision = PromotionDecision(
        lesson_id=args.lesson_id,
        approved=True,
        reason=args.reason,
        decided_by=args.user or "cli-user",
    )

    try:
        lesson = service.promote(decision)
        print(f"✓ Promoted lesson {lesson.id}")
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_memory_reject(args: argparse.Namespace) -> int:
    """Reject a candidate lesson."""
    store = FileBackedLessonStore(Path(args.state_dir) / "memory")
    service = LessonService(store)

    decision = PromotionDecision(
        lesson_id=args.lesson_id,
        approved=False,
        reason=args.reason,
        decided_by=args.user or "cli-user",
    )

    try:
        lesson = service.reject(decision)
        print(f"✗ Rejected lesson {lesson.id}")
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_memory_query(args: argparse.Namespace) -> int:
    """Query lessons by repo, tags, and status."""
    store = FileBackedLessonStore(Path(args.state_dir) / "memory")
    service = LessonService(store)

    # Parse status argument
    status = None
    if args.status != "all":
        try:
            status = LessonStatus(args.status)
        except ValueError:
            print(f"Error: invalid status '{args.status}'", file=sys.stderr)
            return 1

    query = LessonQuery(
        repo=args.repo,
        tags=args.tags if args.tags else [],
        status=status,
        include_expired=args.include_expired,
        limit=args.limit,
    )

    lessons = service.query(query)

    if not lessons:
        print("No matching lessons.")
        return 0

    for lesson in lessons:
        print(f"\n{'=' * 80}")
        print(f"ID:         {lesson.id}")
        print(f"Status:     {lesson.status.value}")
        print(f"Created:    {lesson.created_at}")
        if lesson.repo:
            print(f"Repo:       {lesson.repo}")
        if lesson.tags:
            print(f"Tags:       {', '.join(lesson.tags)}")
        print(f"\nText:\n{lesson.text}")

    print(f"\n{'=' * 80}")
    print(f"\n{len(lessons)} matching lesson(s)")
    return 0


def cmd_memory_cleanup(args: argparse.Namespace) -> int:
    """Expire old candidates and clean up expired lessons."""
    store = FileBackedLessonStore(Path(args.state_dir) / "memory")
    service = LessonService(store)

    # First expire any candidates past their TTL
    expired = service.expire_candidates()
    if expired:
        print(f"Expired {len(expired)} candidate(s)")

    # Then delete old expired lessons
    if args.delete_older_than_days:
        deleted = service.cleanup_expired(older_than_days=args.delete_older_than_days)
        print(f"Deleted {deleted} expired lesson(s) older than {args.delete_older_than_days} days")

    return 0


def cmd_canary_start(args: argparse.Namespace) -> int:
    """Start a new canary deployment."""
    plan = CanaryPlan.model_validate_json(Path(args.plan).read_text())
    run = CanaryRun.model_validate_json(Path(args.run).read_text())

    backend = FakeBackend()
    if args.current_version:
        backend.current_version = args.current_version

    orchestrator = CanaryOrchestrator(backend, state_dir=Path(args.state_dir) / "canary")

    try:
        result = orchestrator.start_canary(plan, run)
        if not args.no_deploy:
            backend.deploy_canary(run.version, plan.stages[0].traffic_percent)
            result.add_decision(
                "deploy_initial",
                f"deployed {run.version} at {plan.stages[0].traffic_percent}% traffic",
            )
            orchestrator._save_run(result)

        print(result.model_dump_json(indent=2))
        return 0
    except Exception as e:
        print(f"Error starting canary: {e}", file=sys.stderr)
        return 1


def cmd_canary_status(args: argparse.Namespace) -> int:
    """Show status of a canary deployment."""
    backend = FakeBackend()
    orchestrator = CanaryOrchestrator(backend, state_dir=Path(args.state_dir) / "canary")

    run = orchestrator.load_run(args.run_id)
    if not run:
        print(f"Run {args.run_id} not found", file=sys.stderr)
        return 1

    print(run.model_dump_json(indent=2))
    return 0


def cmd_canary_advance(args: argparse.Namespace) -> int:
    """Advance a canary deployment to the next stage."""
    plan = CanaryPlan.model_validate_json(Path(args.plan).read_text())

    backend = FakeBackend()
    orchestrator = CanaryOrchestrator(backend, state_dir=Path(args.state_dir) / "canary")

    run = orchestrator.load_run(args.run_id)
    if not run:
        print(f"Run {args.run_id} not found", file=sys.stderr)
        return 1

    try:
        result = orchestrator.advance_stage(plan, run)
        print(result.model_dump_json(indent=2))
        return 0 if result.status not in ("rolled_back", "failed") else 1
    except Exception as e:
        print(f"Error advancing canary: {e}", file=sys.stderr)
        return 1


def cmd_canary_rollback(args: argparse.Namespace) -> int:
    """Manually rollback a canary deployment."""
    backend = FakeBackend()
    orchestrator = CanaryOrchestrator(backend, state_dir=Path(args.state_dir) / "canary")

    run = orchestrator.load_run(args.run_id)
    if not run:
        print(f"Run {args.run_id} not found", file=sys.stderr)
        return 1

    try:
        result = orchestrator.rollback(run, reason=args.reason)
        print(result.model_dump_json(indent=2))
        print(f"\n✓ Rolled back to {run.previous_version}", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Error during rollback: {e}", file=sys.stderr)
        return 1


def cmd_canary_list(args: argparse.Namespace) -> int:
    """List all canary deployments."""
    backend = FakeBackend()
    orchestrator = CanaryOrchestrator(backend, state_dir=Path(args.state_dir) / "canary")

    runs = orchestrator.list_runs()
    if not runs:
        print("No canary runs found")
        return 0

    print(f"{'ID':<24} {'Status':<15} {'Version':<12} {'Stage':<20} {'Started'}")
    print("=" * 100)

    for run in runs:
        started = run.started_at[:19] if run.started_at else "N/A"
        print(
            f"{run.id:<24} {run.status.value:<15} {run.version:<12} "
            f"{run.current_stage or 'N/A':<20} {started}"
        )

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

    # Secrets management commands
    secrets = sub.add_parser("secrets", help="manage task-scoped credentials")
    secrets_sub = secrets.add_subparsers(dest="secrets_action", required=True)

    secrets_mint = secrets_sub.add_parser("mint", help="mint a new credential")
    secrets_mint.add_argument("--task-id", required=True, help="task ID")
    secrets_mint.add_argument("--work-order-id", required=True, help="work order ID")
    secrets_mint.add_argument("--repo-url", required=True, help="repository URL")
    secrets_mint.add_argument("--branch", help="branch restriction")
    secrets_mint.add_argument("--tool-name", required=True, help="tool name (e.g. gh, git)")
    secrets_mint.add_argument("--scopes", nargs="*", help="required scopes")
    secrets_mint.add_argument(
        "--ttl", type=int, default=3600, help="TTL in seconds (default: 3600)"
    )
    secrets_mint.add_argument("--reason", help="reason for minting")
    secrets_mint.add_argument("--state-dir", default=".factory-runs", help="state directory")
    secrets_mint.set_defaults(func=cmd_secrets_mint)

    secrets_list = secrets_sub.add_parser("list", help="list credentials")
    secrets_list.add_argument("--task-id", help="filter by task ID")
    secrets_list.add_argument(
        "--status", choices=[s.value for s in SecretStatus], help="filter by status"
    )
    secrets_list.add_argument("--state-dir", default=".factory-runs", help="state directory")
    secrets_list.set_defaults(func=cmd_secrets_list)

    secrets_status = secrets_sub.add_parser("status", help="show credential status")
    secrets_status.add_argument("credential_id", help="credential ID")
    secrets_status.add_argument("--state-dir", default=".factory-runs", help="state directory")
    secrets_status.set_defaults(func=cmd_secrets_status)

    secrets_revoke = secrets_sub.add_parser("revoke", help="revoke a credential")
    secrets_revoke.add_argument("credential_id", help="credential ID")
    secrets_revoke.add_argument("--state-dir", default=".factory-runs", help="state directory")
    secrets_revoke.set_defaults(func=cmd_secrets_revoke)

    secrets_cleanup = secrets_sub.add_parser("cleanup", help="clean up expired credentials")
    secrets_cleanup.add_argument("--state-dir", default=".factory-runs", help="state directory")
    secrets_cleanup.set_defaults(func=cmd_secrets_cleanup)

    # Memory lessons commands
    memory = sub.add_parser("memory", help="manage memory lessons")
    memory_sub = memory.add_subparsers(dest="memory_action", required=True)

    memory_propose = memory_sub.add_parser("propose", help="propose a new lesson")
    memory_propose.add_argument("--text", required=True, help="lesson text")
    memory_propose.add_argument("--repo", help="repository URL this lesson applies to")
    memory_propose.add_argument("--tags", nargs="+", help="tags for categorization")
    memory_propose.add_argument("--run-id", help="cloud agent run that generated this")
    memory_propose.add_argument("--work-order-id", help="work order that generated this")
    memory_propose.add_argument("--ttl-days", type=int, default=90, help="days until expiry")
    memory_propose.add_argument("--state-dir", default=".factory-runs", help="state directory")
    memory_propose.set_defaults(func=cmd_memory_propose)

    memory_list = memory_sub.add_parser("list", help="list lessons by status")
    memory_list.add_argument(
        "status",
        choices=["candidates", "promoted", "rejected", "expired"],
        help="lesson status to list",
    )
    memory_list.add_argument("--state-dir", default=".factory-runs", help="state directory")
    memory_list.set_defaults(func=cmd_memory_list)

    memory_promote = memory_sub.add_parser("promote", help="promote a candidate lesson")
    memory_promote.add_argument("lesson_id", help="lesson ID to promote")
    memory_promote.add_argument("--reason", required=True, help="reason for promotion")
    memory_promote.add_argument("--user", help="user name (defaults to cli-user)")
    memory_promote.add_argument("--state-dir", default=".factory-runs", help="state directory")
    memory_promote.set_defaults(func=cmd_memory_promote)

    memory_reject = memory_sub.add_parser("reject", help="reject a candidate lesson")
    memory_reject.add_argument("lesson_id", help="lesson ID to reject")
    memory_reject.add_argument("--reason", required=True, help="reason for rejection")
    memory_reject.add_argument("--user", help="user name (defaults to cli-user)")
    memory_reject.add_argument("--state-dir", default=".factory-runs", help="state directory")
    memory_reject.set_defaults(func=cmd_memory_reject)

    memory_query = memory_sub.add_parser("query", help="query lessons")
    memory_query.add_argument("--repo", help="filter by repository URL")
    memory_query.add_argument("--tags", nargs="+", help="filter by tags (must have all)")
    memory_query.add_argument(
        "--status",
        default="promoted",
        help="filter by status (default: promoted, use 'all' for any status)",
    )
    memory_query.add_argument(
        "--include-expired", action="store_true", help="include expired candidates"
    )
    memory_query.add_argument("--limit", type=int, default=100, help="max results")
    memory_query.add_argument("--state-dir", default=".factory-runs", help="state directory")
    memory_query.set_defaults(func=cmd_memory_query)

    memory_cleanup = memory_sub.add_parser("cleanup", help="expire and clean up old lessons")
    memory_cleanup.add_argument(
        "--delete-older-than-days",
        type=int,
        help="delete expired lessons older than N days",
    )
    memory_cleanup.add_argument("--state-dir", default=".factory-runs", help="state directory")
    memory_cleanup.set_defaults(func=cmd_memory_cleanup)

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

    # Canary deployment commands
    canary = sub.add_parser("canary", help="manage canary deployments")
    canary_sub = canary.add_subparsers(dest="canary_action", required=True)

    canary_start = canary_sub.add_parser("start", help="start a new canary deployment")
    canary_start.add_argument("--plan", required=True, help="path to canary plan JSON")
    canary_start.add_argument("--run", required=True, help="path to canary run JSON")
    canary_start.add_argument("--state-dir", default=".factory-runs", help="state directory")
    canary_start.add_argument("--current-version", help="current deployed version")
    canary_start.add_argument(
        "--no-deploy", action="store_true", help="don't deploy, just record state"
    )
    canary_start.set_defaults(func=cmd_canary_start)

    canary_status = canary_sub.add_parser("status", help="show canary deployment status")
    canary_status.add_argument("run_id", help="canary run ID")
    canary_status.add_argument("--state-dir", default=".factory-runs", help="state directory")
    canary_status.set_defaults(func=cmd_canary_status)

    canary_advance = canary_sub.add_parser("advance", help="advance to next stage")
    canary_advance.add_argument("run_id", help="canary run ID")
    canary_advance.add_argument("--plan", required=True, help="path to canary plan JSON")
    canary_advance.add_argument("--state-dir", default=".factory-runs", help="state directory")
    canary_advance.set_defaults(func=cmd_canary_advance)

    canary_rollback = canary_sub.add_parser("rollback", help="manually rollback canary")
    canary_rollback.add_argument("run_id", help="canary run ID")
    canary_rollback.add_argument("--reason", required=True, help="reason for rollback")
    canary_rollback.add_argument("--state-dir", default=".factory-runs", help="state directory")
    canary_rollback.set_defaults(func=cmd_canary_rollback)

    canary_list = canary_sub.add_parser("list", help="list all canary deployments")
    canary_list.add_argument("--state-dir", default=".factory-runs", help="state directory")
    canary_list.set_defaults(func=cmd_canary_list)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
