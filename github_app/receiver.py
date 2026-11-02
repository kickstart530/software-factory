"""Webhook receiver for GitHub App events.

Receives webhooks from GitHub, validates signatures, and dispatches to handlers
that submit Work Orders to Temporal.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from flask import Flask, jsonify, request

logger = logging.getLogger(__name__)


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature.

    Args:
        payload: Raw request body bytes
        signature: X-Hub-Signature-256 header value
        secret: Webhook secret configured in GitHub App

    Returns:
        True if signature is valid
    """
    if not signature.startswith("sha256="):
        return False

    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    received = signature[7:]  # Strip "sha256=" prefix
    return hmac.compare_digest(expected, received)


class WebhookReceiver:
    """Processes GitHub webhooks and submits Work Orders to Temporal."""

    def __init__(self, temporal_client_factory, webhook_secret: str):
        """Initialize receiver.

        Args:
            temporal_client_factory: Callable that returns a Temporal client
            webhook_secret: GitHub App webhook secret for signature verification
        """
        self.temporal_client_factory = temporal_client_factory
        self.webhook_secret = webhook_secret

    def handle_issues(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Handle issues events (opened, labeled, etc.).

        Phase 2a: respond to issues labeled "factory:build" or with /factory commands.

        Args:
            payload: GitHub webhook payload

        Returns:
            Response dict or None if no action taken
        """
        action = payload.get("action")

        # Phase 2a pilot: trigger on "factory:build" label
        if action == "labeled":
            label = payload.get("label", {}).get("name")
            if label == "factory:build":
                return self._submit_work_order_from_issue(payload)

        return None

    def handle_issue_comment(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Handle issue comment events.

        Phase 2a: respond to /factory commands.

        Args:
            payload: GitHub webhook payload

        Returns:
            Response dict or None if no action taken
        """
        action = payload.get("action")
        if action != "created":
            return None

        comment = payload.get("comment", {})
        body = comment.get("body", "").strip()

        # Check for /factory command
        if body.startswith("/factory build") or body.startswith("/factory"):
            return self._submit_work_order_from_issue(payload)

        return None

    def handle_pull_request(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Handle pull request events.

        Phase 2a: monitor PR status for Integrator agent (not implemented yet).

        Args:
            payload: GitHub webhook payload

        Returns:
            Response dict or None if no action taken
        """
        # Phase 2a: log for observability, no action yet
        action = payload.get("action")
        pr_number = payload.get("pull_request", {}).get("number")
        logger.info(f"PR #{pr_number} {action}")
        return None

    def handle_check_run(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Handle check run events (CI status updates).

        Phase 2a: will notify Integrator agent when CI fails (not implemented yet).

        Args:
            payload: GitHub webhook payload

        Returns:
            Response dict or None if no action taken
        """
        # Phase 2a: log for observability, no action yet
        conclusion = payload.get("check_run", {}).get("conclusion")
        logger.info(f"Check run completed: {conclusion}")
        return None

    def _submit_work_order_from_issue(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit a Work Order to Temporal based on an issue.

        Phase 2a: creates a hand-authored Work Order structure from issue title/body.
        Later phases will use the Spec agent to refine the request.

        Args:
            payload: GitHub webhook payload

        Returns:
            Response dict with workflow ID
        """
        issue = payload.get("issue", {})
        repo = payload.get("repository", {})

        # Extract issue details
        issue_number = issue.get("number")
        title = issue.get("title", "")
        body = issue.get("body", "")
        repo_full_name = repo.get("full_name", "")
        repo_url = repo.get("clone_url", "")

        # Phase 2a: create a simplified Work Order
        # In production, this would invoke the Spec agent to refine requirements
        work_order = {
            "id": f"WO-{repo_full_name.replace('/', '-')}-{issue_number}",
            "title": title,
            "description": body or title,
            "repo": {
                "url": repo_url,
                "base_branch": repo.get("default_branch", "main"),
            },
            "acceptance_criteria": [
                {
                    "id": "builds",
                    "description": "Code builds without errors",
                    "verification": "gate",
                    "gate": "build",
                }
            ],
            "risk_tier": "low",
            "requested_by": issue.get("user", {}).get("login", "unknown"),
            "labels": [f"github-issue-{issue_number}"],
        }

        logger.info(f"Submitting Work Order {work_order['id']} to Temporal")

        # TODO: Actually submit to Temporal
        # This is a stub for Phase 2a - full implementation requires:
        # 1. Temporal client connection
        # 2. Start ExecuteTaskDAGWorkflow (after Planner creates the DAG)
        # 3. Return workflow handle

        # For now, return a success response
        return {
            "status": "submitted",
            "work_order_id": work_order["id"],
            "message": "Work Order created (Phase 2a stub - Temporal integration pending)",
        }


def create_webhook_app(
    temporal_client_factory=None,
    webhook_secret: str | None = None,
) -> Flask:
    """Create a Flask app that receives GitHub webhooks.

    Args:
        temporal_client_factory: Factory function that returns a Temporal client
        webhook_secret: GitHub App webhook secret for signature verification

    Returns:
        Configured Flask app

    Example:
        >>> app = create_webhook_app(temporal_client_factory, secret)
        >>> app.run(host="0.0.0.0", port=8080)
    """
    app = Flask(__name__)

    # For Phase 2a, allow running without Temporal (logs only)
    receiver = WebhookReceiver(
        temporal_client_factory or (lambda: None),
        webhook_secret or "dev-secret-change-in-production",
    )

    @app.route("/webhooks/github", methods=["POST"])
    def github_webhook():
        """Handle incoming GitHub webhooks."""
        # Verify signature
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(request.data, signature, receiver.webhook_secret):
            logger.warning("Invalid webhook signature")
            return jsonify({"error": "Invalid signature"}), 401

        # Parse event
        event_type = request.headers.get("X-GitHub-Event", "")
        payload = request.get_json()

        logger.info(f"Received {event_type} event")

        # Dispatch to handler
        result = None
        if event_type == "issues":
            result = receiver.handle_issues(payload)
        elif event_type == "issue_comment":
            result = receiver.handle_issue_comment(payload)
        elif event_type == "pull_request":
            result = receiver.handle_pull_request(payload)
        elif event_type == "check_run":
            result = receiver.handle_check_run(payload)
        elif event_type == "ping":
            return jsonify({"message": "pong"}), 200
        else:
            logger.info(f"Unhandled event type: {event_type}")

        if result:
            return jsonify(result), 200

        return jsonify({"message": "received"}), 200

    @app.route("/health", methods=["GET"])
    def health():
        """Health check endpoint."""
        return jsonify({"status": "healthy"}), 200

    return app
