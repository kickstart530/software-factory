"""Tests for the approval service (Phase 2b)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from factory.harness.approvals import FileBasedApprovalService
from factory.schemas import ApprovalDecision, ApprovalRequest, ApprovalStatus


@pytest.fixture
def approvals_dir(tmp_path: Path) -> Path:
    return tmp_path / "approvals"


@pytest.fixture
def service(approvals_dir: Path) -> FileBasedApprovalService:
    return FileBasedApprovalService(approvals_dir)


def test_request_approval_creates_pending_request(service: FileBasedApprovalService):
    request = ApprovalRequest(
        id="req-001",
        task_id="task-001",
        work_order_id="wo-001",
        agent="implementer",
        tool_name="run_command",
        tool_args={"command": "terraform apply"},
        reason="deployment requires approval",
        risk_factors=["deployment", "infrastructure"],
    )

    result = service.request_approval(request)

    assert result.status == ApprovalStatus.PENDING
    assert result.requested_at is not None
    assert result.expires_at is not None

    # Should be in pending directory
    pending_path = service.pending_dir / "req-001.json"
    assert pending_path.exists()


def test_list_pending_returns_pending_requests(service: FileBasedApprovalService):
    req1 = ApprovalRequest(
        id="req-001",
        task_id="task-001",
        work_order_id="wo-001",
        agent="implementer",
        tool_name="write_file",
        tool_args={"path": ".github/workflows/deploy.yml"},
        reason="CI/CD modification requires approval",
    )
    req2 = ApprovalRequest(
        id="req-002",
        task_id="task-002",
        work_order_id="wo-001",
        agent="implementer",
        tool_name="run_command",
        tool_args={"command": "kubectl apply"},
        reason="deployment requires approval",
    )

    service.request_approval(req1)
    service.request_approval(req2)

    pending = service.list_pending()
    assert len(pending) == 2
    assert {r.id for r in pending} == {"req-001", "req-002"}


def test_record_decision_approves_request(service: FileBasedApprovalService):
    request = ApprovalRequest(
        id="req-001",
        task_id="task-001",
        work_order_id="wo-001",
        agent="implementer",
        tool_name="run_command",
        tool_args={"command": "terraform apply"},
        reason="deployment requires approval",
    )
    service.request_approval(request)

    decision = ApprovalDecision(
        request_id="req-001",
        approved=True,
        reason="reviewed and approved",
        decided_by="alice",
    )
    service.record_decision(decision)

    # Should be moved to decided
    assert not (service.pending_dir / "req-001.json").exists()
    assert (service.decided_dir / "req-001.json").exists()

    # Request should be updated
    updated = service.get_request("req-001")
    assert updated is not None
    assert updated.status == ApprovalStatus.APPROVED
    assert updated.decided_by == "alice"
    assert updated.decision_reason == "reviewed and approved"


def test_record_decision_denies_request(service: FileBasedApprovalService):
    request = ApprovalRequest(
        id="req-001",
        task_id="task-001",
        work_order_id="wo-001",
        agent="implementer",
        tool_name="run_command",
        tool_args={"command": "rm -rf /data"},
        reason="dangerous command requires approval",
    )
    service.request_approval(request)

    decision = ApprovalDecision(
        request_id="req-001",
        approved=False,
        reason="too dangerous",
        decided_by="bob",
    )
    service.record_decision(decision)

    updated = service.get_request("req-001")
    assert updated is not None
    assert updated.status == ApprovalStatus.DENIED
    assert updated.decided_by == "bob"


def test_expired_requests_not_in_pending_list(service: FileBasedApprovalService):
    # Create a request that expired 1 hour ago
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    request = ApprovalRequest(
        id="req-001",
        task_id="task-001",
        work_order_id="wo-001",
        agent="implementer",
        tool_name="run_command",
        tool_args={"command": "deploy"},
        reason="test",
        expires_at=past.isoformat(),
        requested_at=past.isoformat(),
    )
    service.request_approval(request)

    pending = service.list_pending()
    assert len(pending) == 0

    # Should be moved to decided with EXPIRED status
    expired = service.get_request("req-001")
    assert expired is not None
    assert expired.status == ApprovalStatus.EXPIRED


def test_cannot_decide_non_pending_request(service: FileBasedApprovalService):
    request = ApprovalRequest(
        id="req-001",
        task_id="task-001",
        work_order_id="wo-001",
        agent="implementer",
        tool_name="run_command",
        tool_args={"command": "deploy"},
        reason="test",
    )
    service.request_approval(request)

    # Approve it first
    decision1 = ApprovalDecision(
        request_id="req-001",
        approved=True,
        reason="ok",
        decided_by="alice",
    )
    service.record_decision(decision1)

    # Try to decide again - should fail
    decision2 = ApprovalDecision(
        request_id="req-001",
        approved=False,
        reason="changed my mind",
        decided_by="bob",
    )
    with pytest.raises(ValueError, match="cannot decide"):
        service.record_decision(decision2)


def test_get_request_not_found(service: FileBasedApprovalService):
    result = service.get_request("nonexistent")
    assert result is None
