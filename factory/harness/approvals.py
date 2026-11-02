"""Human approval service for high-risk operations (Phase 2b).

Approval flow:
1. Harness detects a tool call needs approval (policy says requires_approval=True)
2. Harness writes ApprovalRequest to `.factory-runs/{run_id}/approvals/{request_id}.json`
3. Harness blocks until approval file appears or timeout
4. CLI operator uses `factory approve` to review and approve/deny
5. Harness reads decision and proceeds or denies the tool call

For Phase 2b this is file-based. A future phase can use an approval service API.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from factory.schemas import ApprovalDecision, ApprovalRequest, ApprovalStatus, Id


class ApprovalService(Protocol):
    """Protocol for approval backends (file-based for Phase 2b, API later)."""

    def request_approval(self, request: ApprovalRequest) -> ApprovalRequest: ...
    def wait_for_decision(
        self, request_id: Id, timeout_s: float = 3600, poll_interval_s: float = 5
    ) -> ApprovalDecision | None: ...
    def list_pending(self) -> list[ApprovalRequest]: ...
    def get_request(self, request_id: Id) -> ApprovalRequest | None: ...
    def record_decision(self, decision: ApprovalDecision) -> None: ...


class FileBasedApprovalService:
    """File-based approval service for CLI workflow."""

    def __init__(self, approvals_dir: Path):
        self.approvals_dir = approvals_dir
        self.approvals_dir.mkdir(parents=True, exist_ok=True)
        self.pending_dir = self.approvals_dir / "pending"
        self.decided_dir = self.approvals_dir / "decided"
        self.pending_dir.mkdir(exist_ok=True)
        self.decided_dir.mkdir(exist_ok=True)

    def _request_path(self, request_id: Id, pending: bool = True) -> Path:
        subdir = self.pending_dir if pending else self.decided_dir
        return subdir / f"{request_id}.json"

    def request_approval(self, request: ApprovalRequest) -> ApprovalRequest:
        """Write an approval request and return it."""
        request.requested_at = datetime.now(UTC).isoformat()
        request.status = ApprovalStatus.PENDING
        # Set default expiry to 24 hours from now
        if not request.expires_at:
            expires = datetime.now(UTC) + timedelta(hours=24)
            request.expires_at = expires.isoformat()

        path = self._request_path(request.id, pending=True)
        path.write_text(request.model_dump_json(indent=2))
        return request

    def wait_for_decision(
        self, request_id: Id, timeout_s: float = 3600, poll_interval_s: float = 5
    ) -> ApprovalDecision | None:
        """Block until a decision is recorded or timeout."""
        start = time.time()
        decision_path = self.approvals_dir / "decisions" / f"{request_id}.json"

        while time.time() - start < timeout_s:
            if decision_path.exists():
                decision_data = json.loads(decision_path.read_text())
                return ApprovalDecision.model_validate(decision_data)

            # Check if request expired
            request = self.get_request(request_id)
            if request and request.expires_at:
                expires = datetime.fromisoformat(request.expires_at)
                if datetime.now(UTC) > expires:
                    # Mark as expired
                    request.status = ApprovalStatus.EXPIRED
                    self._move_to_decided(request)
                    return None

            time.sleep(poll_interval_s)

        return None

    def list_pending(self) -> list[ApprovalRequest]:
        """List all pending approval requests."""
        requests = []
        for path in self.pending_dir.glob("*.json"):
            data = json.loads(path.read_text())
            request = ApprovalRequest.model_validate(data)
            # Check expiry
            if request.expires_at:
                expires = datetime.fromisoformat(request.expires_at)
                if datetime.now(UTC) > expires:
                    request.status = ApprovalStatus.EXPIRED
                    self._move_to_decided(request)
                    continue
            requests.append(request)
        return sorted(requests, key=lambda r: r.requested_at)

    def get_request(self, request_id: Id) -> ApprovalRequest | None:
        """Get a specific request (pending or decided)."""
        pending_path = self._request_path(request_id, pending=True)
        if pending_path.exists():
            data = json.loads(pending_path.read_text())
            return ApprovalRequest.model_validate(data)

        decided_path = self._request_path(request_id, pending=False)
        if decided_path.exists():
            data = json.loads(decided_path.read_text())
            return ApprovalRequest.model_validate(data)

        return None

    def record_decision(self, decision: ApprovalDecision) -> None:
        """Record a human decision and move the request to decided."""
        request = self.get_request(decision.request_id)
        if not request:
            raise ValueError(f"approval request {decision.request_id} not found")

        if request.status != ApprovalStatus.PENDING:
            raise ValueError(f"request {decision.request_id} is {request.status}, cannot decide")

        # Update request with decision
        request.status = ApprovalStatus.APPROVED if decision.approved else ApprovalStatus.DENIED
        request.decided_at = datetime.now(UTC).isoformat()
        request.decided_by = decision.decided_by
        request.decision_reason = decision.reason

        # Write decision file for wait_for_decision to pick up
        decision_path = self.approvals_dir / "decisions" / f"{decision.request_id}.json"
        decision_path.parent.mkdir(exist_ok=True)
        decision_path.write_text(decision.model_dump_json(indent=2))

        # Move request to decided
        self._move_to_decided(request)

    def _move_to_decided(self, request: ApprovalRequest) -> None:
        """Move a request from pending to decided."""
        pending_path = self._request_path(request.id, pending=True)
        decided_path = self._request_path(request.id, pending=False)

        decided_path.write_text(request.model_dump_json(indent=2))
        if pending_path.exists():
            pending_path.unlink()
