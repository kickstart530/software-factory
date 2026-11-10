"""Command line entry point: ``factory <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evals import (
    ConfigVariant,
    EvalGate,
    ReplayRunner,
    load_golden_tasks,
    print_gate_report,
    save_gate_report,
)
from factory.agents.spec import AgentCatalog
from factory.control import KillSwitch
from factory.control.budget_ledger import FileBudgetLedger
from factory.control.cost_dashboard import CostDashboard
from factory.control.kill_switch import FileKillSwitchStore
from factory.golden_gen import (
    PRMetadata,
    generate_candidate_task,
    load_candidate,
    promote_candidate,
    reject_candidate,
    save_candidate,
)
from factory.harness import (
    AgentHarness,
    AnthropicModelClient,
    ExperimentConfig,
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
from factory.repo_config import EffectiveAgentConfig, RepoConfig
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
    workspace = Path(args.workspace)
    state_dir = Path(args.state_dir)
    config = ModelConfig.load(Path(args.models))
    experiments_path = Path(args.experiments)
    experiments = (
        ExperimentConfig.load(experiments_path) if experiments_path.exists() else ExperimentConfig()
    )

    # Apply repo-level overrides to the agent spec
    repo_cfg = RepoConfig.load(workspace)
    effective_budget = repo_cfg.apply_to_budget(task.agent, spec.budget)
    effective_tier = repo_cfg.get_model_tier(task.agent, spec.model_tier)
    effective_gates = repo_cfg.get_gates(task.agent, spec.gates)
    effective_max_attempts = repo_cfg.get_max_gate_attempts(task.agent, spec.max_gate_attempts)

    # Create a modified spec with effective values
    from factory.agents.spec import AgentSpec

    effective_spec = AgentSpec(
        name=spec.name,
        version=spec.version,
        description=spec.description,
        model_tier=effective_tier,
        system_prompt=spec.system_prompt,
        tools=spec.tools,
        policy=spec.policy,
        budget=effective_budget,
        gates=effective_gates,
        max_gate_attempts=effective_max_attempts,
        output_schema=spec.output_schema,
        sub_agents=spec.sub_agents,
    )

    deps = HarnessDeps(
        model=AnthropicModelClient(config, experiments=experiments),
        model_config=config,
        checkpoints=FileCheckpointStore(state_dir / "checkpoints"),
        tracer=JsonlTracer(state_dir / "traces" / f"{task.id}.jsonl", task_id=task.id),
        kill_switch=KillSwitch(FileKillSwitchStore(state_dir / "kill_switch")),
    )
    ctx = ToolContext(workspace=workspace, artifacts_dir=state_dir / "artifacts" / task.id)
    return AgentHarness(
        effective_spec, catalog.system_prompt(spec), deps, ctx, risk_tier=RiskTier(args.risk_tier)
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


def cmd_golden_generate(args: argparse.Namespace) -> int:
    """Generate a candidate golden task from PR fixture."""
    import json
    from pathlib import Path

    # Load PR metadata from fixture file
    pr_file = Path(args.pr_fixture)
    if not pr_file.exists():
        print(f"Error: PR fixture file not found: {pr_file}", file=sys.stderr)
        return 1

    try:
        pr_data = json.loads(pr_file.read_text())
        pr_metadata = PRMetadata(**pr_data)
    except Exception as e:
        print(f"Error: Failed to parse PR fixture: {e}", file=sys.stderr)
        return 1

    # Generate candidate
    try:
        agent_catalog = Path(args.agents) if args.agents else None
        candidate = generate_candidate_task(
            pr_metadata,
            agent_catalog_path=agent_catalog,
            custom_agent=args.agent,
        )
    except Exception as e:
        print(f"Error: Failed to generate candidate: {e}", file=sys.stderr)
        return 1

    # Save candidate
    candidates_dir = Path(args.candidates_dir)
    filepath = save_candidate(candidate, candidates_dir)

    print(f"✓ Generated candidate golden task from PR #{pr_metadata.number}")
    print(f"  ID: {candidate.id}")
    print(f"  Agent: {candidate.task['agent']}")
    print(f"  Complexity: {candidate.metadata['complexity']['complexity']}")
    print(f"  Saved to: {filepath}")
    print("\nReview with: factory golden list")
    print(f"Promote with: factory golden promote {candidate.id} --reviewer <name>")

    return 0


def cmd_golden_list(args: argparse.Namespace) -> int:
    """List all candidate golden tasks."""
    from pathlib import Path

    candidates_dir = Path(args.candidates_dir)
    if not candidates_dir.exists():
        print("No candidates directory found.")
        return 0

    candidates = []
    for filepath in sorted(candidates_dir.glob("*.json")):
        try:
            candidate = load_candidate(filepath)
            candidates.append((filepath, candidate))
        except Exception as e:
            print(f"Warning: Failed to load {filepath}: {e}", file=sys.stderr)

    if not candidates:
        print(f"No candidate golden tasks found in {candidates_dir}")
        return 0

    # Filter by status if specified
    if args.status:
        candidates = [(f, c) for f, c in candidates if c.status == args.status]

    if not candidates:
        print(f"No candidates with status '{args.status}' found.")
        return 0

    print(f"\n{'=' * 80}")
    print(f"Candidate Golden Tasks ({len(candidates)} found)")
    print(f"{'=' * 80}\n")

    for filepath, candidate in candidates:
        status_mark = {
            "pending_review": "⏳",
            "approved": "✓",
            "rejected": "✗",
        }.get(candidate.status, "?")

        print(f"{status_mark} {candidate.id}")
        print(f"  PR #{candidate.pr_number}: {candidate.task['title']}")
        print(f"  Agent: {candidate.task['agent']}")
        print(f"  Status: {candidate.status}")
        print(f"  Created: {candidate.created_at}")
        if candidate.reviewer:
            print(f"  Reviewed by: {candidate.reviewer} at {candidate.reviewed_at}")
        if candidate.review_notes:
            print(f"  Notes: {candidate.review_notes}")
        print(f"  File: {filepath.name}")
        print()

    if args.json:
        json_output = [
            {
                "id": c.id,
                "pr_number": c.pr_number,
                "title": c.task["title"],
                "agent": c.task["agent"],
                "status": c.status,
                "created_at": c.created_at,
                "reviewer": c.reviewer,
                "reviewed_at": c.reviewed_at,
                "filepath": str(f),
            }
            for f, c in candidates
        ]
        Path(args.json).write_text(json.dumps(json_output, indent=2))
        print(f"Wrote listing to {args.json}")

    return 0


def cmd_golden_promote(args: argparse.Namespace) -> int:
    """Promote a candidate golden task to the active set."""
    from pathlib import Path

    candidates_dir = Path(args.candidates_dir)
    golden_dir = Path(args.golden_dir)

    # Find candidate by ID
    candidate = None
    for filepath in candidates_dir.glob("*.json"):
        try:
            c = load_candidate(filepath)
            if c.id == args.candidate_id:
                candidate = c
                break
        except Exception:
            continue

    if not candidate:
        print(f"Error: Candidate {args.candidate_id} not found", file=sys.stderr)
        return 1

    if candidate.status == "rejected":
        print(
            f"Error: Cannot promote rejected candidate. "
            f"Rejected by {candidate.reviewer}: {candidate.review_notes}",
            file=sys.stderr,
        )
        return 1

    # Promote
    try:
        golden_path = promote_candidate(
            candidate,
            golden_dir,
            candidates_dir,
            args.reviewer,
            args.notes,
        )
        print(f"✓ Promoted candidate {candidate.id} to active golden set")
        print(f"  PR #{candidate.pr_number}: {candidate.task['title']}")
        print(f"  Promoted by: {args.reviewer}")
        print(f"  Golden task: {golden_path}")
        print(
            "\nNote: This golden task has placeholder model responses. "
            "Run it once to capture real responses."
        )
        return 0
    except Exception as e:
        print(f"Error: Failed to promote candidate: {e}", file=sys.stderr)
        return 1


def cmd_golden_reject(args: argparse.Namespace) -> int:
    """Reject a candidate golden task."""
    from pathlib import Path

    candidates_dir = Path(args.candidates_dir)

    # Find candidate by ID
    candidate = None
    for filepath in candidates_dir.glob("*.json"):
        try:
            c = load_candidate(filepath)
            if c.id == args.candidate_id:
                candidate = c
                break
        except Exception:
            continue

    if not candidate:
        print(f"Error: Candidate {args.candidate_id} not found", file=sys.stderr)
        return 1

    if candidate.status == "rejected":
        print(f"Candidate {args.candidate_id} is already rejected")
        return 0

    # Reject
    try:
        reject_candidate(candidate, candidates_dir, args.reviewer, args.reason)
        print(f"✗ Rejected candidate {candidate.id}")
        print(f"  PR #{candidate.pr_number}: {candidate.task['title']}")
        print(f"  Rejected by: {args.reviewer}")
        print(f"  Reason: {args.reason}")
        return 0
    except Exception as e:
        print(f"Error: Failed to reject candidate: {e}", file=sys.stderr)
        return 1


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


def cmd_repo_config_validate(args: argparse.Namespace) -> int:
    """Validate .factory/repo.yaml in a workspace."""
    workspace = Path(args.workspace)
    config_path = workspace / ".factory" / "repo.yaml"

    if not config_path.is_file():
        print(f"No .factory/repo.yaml found in {workspace} (using factory defaults)")
        return 0

    try:
        repo_cfg = RepoConfig.load(workspace)
        print(f"✓ Valid repo config from {config_path}")
        if repo_cfg.description:
            print(f"  Description: {repo_cfg.description}")
        if repo_cfg.conventions:
            print(f"  Conventions: {repo_cfg.conventions}")
        if repo_cfg.agents:
            print(f"  Agent overrides: {', '.join(sorted(repo_cfg.agents.keys()))}")
        else:
            print("  No agent overrides (using factory defaults)")
        return 0
    except Exception as e:
        print(f"✗ Invalid repo config: {e}", file=sys.stderr)
        return 1


def cmd_repo_config_show(args: argparse.Namespace) -> int:
    """Show effective config for an agent in a workspace."""
    workspace = Path(args.workspace)
    agent_name = args.agent

    # Load global agent spec
    catalog = AgentCatalog(Path(args.agents))
    try:
        spec = catalog[agent_name]
    except KeyError:
        print(f"✗ Agent {agent_name!r} not found in {args.agents}", file=sys.stderr)
        print(f"  Available agents: {', '.join(catalog.names())}", file=sys.stderr)
        return 1

    # Load repo config
    repo_cfg = RepoConfig.load(workspace)

    # Compute effective config
    effective_tier = repo_cfg.get_model_tier(agent_name, spec.model_tier)
    effective_budget = repo_cfg.apply_to_budget(agent_name, spec.budget)
    effective_gates = repo_cfg.get_gates(agent_name, spec.gates)
    effective_max_attempts = repo_cfg.get_max_gate_attempts(agent_name, spec.max_gate_attempts)

    # Determine source
    has_override = agent_name in repo_cfg.agents
    if not has_override:
        source = "default"
    else:
        override = repo_cfg.agents[agent_name]
        has_any = any(
            [
                override.model_tier is not None,
                override.budget is not None,
                override.gates is not None,
                override.max_gate_attempts is not None,
            ]
        )
        source = "repo-override" if has_any else "default"

    effective = EffectiveAgentConfig(
        agent_name=agent_name,
        model_tier=effective_tier,
        budget=effective_budget,
        gates=effective_gates,
        max_gate_attempts=effective_max_attempts,
        conventions=repo_cfg.conventions,
        source=source,
    )

    if args.json:
        print(effective.model_dump_json(indent=2))
    else:
        print(f"Effective configuration for agent '{agent_name}' in {workspace}")
        print(f"{'=' * 80}")
        print(f"Source:            {effective.source}")
        print(f"Model tier:        {effective.model_tier}")
        print("Budget:")
        print(f"  tokens:          {effective.budget.tokens:,}")
        print(f"  usd:             ${effective.budget.usd:.2f}")
        print(f"  wall_clock_s:    {effective.budget.wall_clock_s:,}s")
        print(f"  turns:           {effective.budget.turns}")
        print(f"  tool_calls:      {effective.budget.tool_calls}")
        print(f"Gates:             {', '.join(effective.gates) if effective.gates else '(none)'}")
        print(f"Max gate attempts: {effective.max_gate_attempts}")
        if effective.conventions:
            print(f"Conventions:       {effective.conventions}")

        # Show what was overridden
        if has_override:
            print(f"\n{'=' * 80}")
            print("Overrides from .factory/repo.yaml:")
            override = repo_cfg.agents[agent_name]
            if override.model_tier is not None:
                print(f"  model_tier: {spec.model_tier} → {override.model_tier}")
            if override.budget:
                print("  budget overrides:")
                if override.budget.tokens is not None:
                    print(f"    tokens: {spec.budget.tokens:,} → {override.budget.tokens:,}")
                if override.budget.usd is not None:
                    print(f"    usd: ${spec.budget.usd:.2f} → ${override.budget.usd:.2f}")
                if override.budget.wall_clock_s is not None:
                    print(
                        f"    wall_clock_s: {spec.budget.wall_clock_s:,}s → "
                        f"{override.budget.wall_clock_s:,}s"
                    )
                if override.budget.turns is not None:
                    print(f"    turns: {spec.budget.turns} → {override.budget.turns}")
                if override.budget.tool_calls is not None:
                    print(
                        f"    tool_calls: {spec.budget.tool_calls} → {override.budget.tool_calls}"
                    )
            if override.gates is not None:
                print(f"  gates: {spec.gates} → {override.gates}")
            if override.max_gate_attempts is not None:
                print(
                    f"  max_gate_attempts: {spec.max_gate_attempts} → {override.max_gate_attempts}"
                )

    return 0


def cmd_evals_gate(args: argparse.Namespace) -> int:
    """Run eval gate to compare baseline vs candidate configurations."""
    golden_dir = Path(args.golden_dir)
    workspace_root = Path(args.workspace_root)
    artifacts_root = Path(args.artifacts_root)

    tasks = load_golden_tasks(golden_dir)
    if not tasks:
        print(f"No golden tasks found in {golden_dir}", file=sys.stderr)
        return 1

    baseline = ConfigVariant(
        name="baseline",
        agents_dir=Path(args.baseline_agents) if args.baseline_agents else None,
        models_config=Path(args.baseline_models) if args.baseline_models else None,
    )

    candidate = ConfigVariant(
        name="candidate",
        agents_dir=Path(args.candidate_agents) if args.candidate_agents else None,
        models_config=Path(args.candidate_models) if args.candidate_models else None,
    )

    gate = EvalGate(baseline, candidate, tasks, workspace_root, artifacts_root)
    comparison = gate.run()

    print_gate_report(comparison)

    if args.json:
        save_gate_report(comparison, Path(args.json))

    return 1 if comparison.has_regressions else 0


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


def cmd_experiments_list(args: argparse.Namespace) -> int:
    """List all A/B experiments with their status."""
    experiments_path = Path(args.experiments)
    if not experiments_path.exists():
        print(f"No experiments file found at {experiments_path}")
        return 0

    config = ExperimentConfig.load(experiments_path)
    if not config.experiments:
        print("No experiments defined")
        return 0

    print(f"{'=' * 80}")
    print(f"A/B Experiments from {experiments_path}")
    print(f"{'=' * 80}\n")

    for exp_id, exp in config.experiments.items():
        status = "ENABLED" if exp.enabled else "disabled"
        print(f"• {exp_id} [{status}]")
        print(f"  Tier:      {exp.tier}")
        print(f"  Sticky by: {exp.sticky_by or 'none (random per-call)'}")
        print(f"  Variants:  {len(exp.variants)}")
        for variant_name, variant in exp.variants.items():
            print(
                f"    - {variant_name}: weight={variant.weight} "
                f"model={variant.model} effort={variant.effort}"
            )
        print()

    enabled = sum(1 for e in config.experiments.values() if e.enabled)
    print(f"Total: {len(config.experiments)} experiments ({enabled} enabled)")
    return 0


def cmd_experiments_show(args: argparse.Namespace) -> int:
    """Show details of a specific experiment."""
    experiments_path = Path(args.experiments)
    if not experiments_path.exists():
        print(f"No experiments file found at {experiments_path}")
        return 1

    config = ExperimentConfig.load(experiments_path)
    exp = config.experiments.get(args.experiment_id)
    if not exp:
        print(f"Experiment '{args.experiment_id}' not found")
        return 1

    print(json.dumps(exp.model_dump(), indent=2))
    return 0


def cmd_experiments_test_assignment(args: argparse.Namespace) -> int:
    """Test which variant would be assigned for a given key."""
    experiments_path = Path(args.experiments)
    if not experiments_path.exists():
        print(f"No experiments file found at {experiments_path}")
        return 1

    config = ExperimentConfig.load(experiments_path)
    exp = config.experiments.get(args.experiment_id)
    if not exp:
        print(f"Experiment '{args.experiment_id}' not found")
        return 1

    if not exp.enabled:
        print(f"Note: experiment '{args.experiment_id}' is currently disabled")

    assignment_key = args.assignment_key
    variant_name, variant = exp.select_variant(assignment_key)

    print(f"Experiment: {args.experiment_id}")
    print(f"Assignment key: {assignment_key}")
    print(f"Sticky by: {exp.sticky_by or 'none (random per-call)'}")
    print(f"\nAssigned variant: {variant_name}")
    print(f"  Model:  {variant.model}")
    print(f"  Effort: {variant.effort}")
    print(f"  Weight: {variant.weight}")

    # Show probability distribution
    total_weight = sum(v.weight for v in exp.variants.values())
    print("\nVariant distribution:")
    for name, var in exp.variants.items():
        prob = (var.weight / total_weight) * 100
        marker = "←" if name == variant_name else " "
        print(f"  {marker} {name}: {prob:.1f}% (weight {var.weight})")

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory", description="Software factory harness CLI")
    p.add_argument("--agents", default=str(REPO_ROOT / "agents"), help="agent definitions dir")
    p.add_argument("--models", default=str(REPO_ROOT / "config" / "models.yaml"))
    p.add_argument(
        "--experiments",
        default=str(REPO_ROOT / "config" / "experiments.yaml"),
        help="A/B experiments configuration",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("schemas", help="export JSON schemas")
    s.add_argument("action", choices=["export"])
    s.add_argument("--out", default=str(REPO_ROOT / "schemas"))
    s.set_defaults(func=cmd_schemas_export)

    a = sub.add_parser("agents", help="validate agent definitions")
    a.add_argument("action", choices=["validate"])
    a.set_defaults(func=cmd_agents_validate)

    # Repo config commands
    repo = sub.add_parser("repo-config", help="validate and inspect per-repo configuration")
    repo_sub = repo.add_subparsers(dest="repo_action", required=True)

    repo_validate = repo_sub.add_parser("validate", help="validate .factory/repo.yaml")
    repo_validate.add_argument("--workspace", required=True, help="repository checkout path")
    repo_validate.set_defaults(func=cmd_repo_config_validate)

    repo_show = repo_sub.add_parser(
        "show", help="show effective config for an agent in a workspace"
    )
    repo_show.add_argument("--workspace", required=True, help="repository checkout path")
    repo_show.add_argument("--agent", required=True, help="agent name")
    repo_show.add_argument("--json", action="store_true", help="output as JSON")
    repo_show.set_defaults(func=cmd_repo_config_show)

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

    # Experiments management commands
    experiments = sub.add_parser("experiments", help="manage A/B experiments")
    experiments_sub = experiments.add_subparsers(dest="experiments_action", required=True)

    exp_list = experiments_sub.add_parser("list", help="list all A/B experiments")
    exp_list.set_defaults(func=cmd_experiments_list)

    exp_show = experiments_sub.add_parser("show", help="show details of a specific experiment")
    exp_show.add_argument("experiment_id", help="experiment ID")
    exp_show.set_defaults(func=cmd_experiments_show)

    exp_test = experiments_sub.add_parser(
        "test-assignment", help="test which variant would be assigned"
    )
    exp_test.add_argument("experiment_id", help="experiment ID")
    exp_test.add_argument("assignment_key", help="work_order_id or tenant_id to test")
    exp_test.set_defaults(func=cmd_experiments_test_assignment)

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
    evals_sub = e.add_subparsers(dest="evals_action", required=True)

    # evals replay subcommand
    replay = evals_sub.add_parser("replay", help="replay golden tasks")
    replay.add_argument(
        "--golden-dir",
        default=str(REPO_ROOT / "evals" / "golden"),
        help="directory with golden task JSON files",
    )
    replay.add_argument(
        "--workspace-root",
        default=".factory-evals/workspaces",
        help="root directory for test workspaces",
    )
    replay.add_argument(
        "--artifacts-root",
        default=".factory-evals/artifacts",
        help="root directory for test artifacts",
    )
    replay.add_argument("--json", help="write detailed results to JSON file")
    replay.set_defaults(func=cmd_evals_replay)

    # evals gate subcommand
    gate = evals_sub.add_parser(
        "gate",
        help="compare baseline vs candidate prompt/model changes",
    )
    gate.add_argument(
        "--golden-dir",
        default=str(REPO_ROOT / "evals" / "golden"),
        help="directory with golden task JSON files",
    )
    gate.add_argument(
        "--workspace-root",
        default=".factory-evals-gate/workspaces",
        help="root directory for test workspaces",
    )
    gate.add_argument(
        "--artifacts-root",
        default=".factory-evals-gate/artifacts",
        help="root directory for test artifacts",
    )
    gate.add_argument(
        "--baseline-agents",
        help="baseline agents directory (defaults to current agents/)",
    )
    gate.add_argument(
        "--baseline-models",
        help="baseline models config (defaults to current config/models.yaml)",
    )
    gate.add_argument(
        "--candidate-agents",
        help="candidate agents directory (required if testing agent changes)",
    )
    gate.add_argument(
        "--candidate-models",
        help="candidate models config (required if testing model changes)",
    )
    gate.add_argument(
        "--json",
        help="write detailed gate report to JSON file",
    )
    gate.set_defaults(func=cmd_evals_gate)

    # Golden task management commands
    golden = sub.add_parser("golden", help="manage golden task candidates")
    golden_sub = golden.add_subparsers(dest="golden_action", required=True)

    golden_gen = golden_sub.add_parser(
        "generate", help="generate candidate golden task from PR fixture"
    )
    golden_gen.add_argument("pr_fixture", help="path to PR fixture JSON file")
    golden_gen.add_argument(
        "--candidates-dir",
        default=str(REPO_ROOT / "evals" / "candidates"),
        help="directory for candidate golden tasks",
    )
    golden_gen.add_argument("--agent", help="override inferred agent type")
    golden_gen.set_defaults(func=cmd_golden_generate)

    golden_list = golden_sub.add_parser("list", help="list candidate golden tasks")
    golden_list.add_argument(
        "--candidates-dir",
        default=str(REPO_ROOT / "evals" / "candidates"),
        help="directory for candidate golden tasks",
    )
    golden_list.add_argument(
        "--status",
        choices=["pending_review", "approved", "rejected"],
        help="filter by status",
    )
    golden_list.add_argument("--json", help="write listing to JSON file")
    golden_list.set_defaults(func=cmd_golden_list)

    golden_promote = golden_sub.add_parser("promote", help="promote candidate to active golden set")
    golden_promote.add_argument("candidate_id", help="candidate ID to promote")
    golden_promote.add_argument(
        "--reviewer", required=True, help="name of reviewer approving promotion"
    )
    golden_promote.add_argument("--notes", help="review notes")
    golden_promote.add_argument(
        "--candidates-dir",
        default=str(REPO_ROOT / "evals" / "candidates"),
        help="directory for candidate golden tasks",
    )
    golden_promote.add_argument(
        "--golden-dir",
        default=str(REPO_ROOT / "evals" / "golden"),
        help="directory for active golden tasks",
    )
    golden_promote.set_defaults(func=cmd_golden_promote)

    golden_reject = golden_sub.add_parser("reject", help="reject a candidate golden task")
    golden_reject.add_argument("candidate_id", help="candidate ID to reject")
    golden_reject.add_argument("--reviewer", required=True, help="name of reviewer")
    golden_reject.add_argument("--reason", required=True, help="reason for rejection")
    golden_reject.add_argument(
        "--candidates-dir",
        default=str(REPO_ROOT / "evals" / "candidates"),
        help="directory for candidate golden tasks",
    )
    golden_reject.set_defaults(func=cmd_golden_reject)

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


# Commands that run agents: the API key must not stay in their process environment.
_AGENT_COMMANDS = {"run", "answer", "sandbox-entry"}


def main(argv: list[str] | None = None) -> int:
    if argv is None and len(sys.argv) > 1 and sys.argv[1] in _AGENT_COMMANDS:
        from factory.harness.credentials import reexec_without_key

        reexec_without_key()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
