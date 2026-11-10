"""Tests for GitHub App webhook receiver and API client."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from github_app.receiver import create_webhook_app, verify_signature


def test_verify_signature_valid():
    """Test signature verification with valid signature."""
    secret = "test-secret"
    payload = b'{"test": "data"}'
    expected_sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    signature = f"sha256={expected_sig}"

    assert verify_signature(payload, signature, secret) is True


def test_verify_signature_invalid():
    """Test signature verification with invalid signature."""
    secret = "test-secret"
    payload = b'{"test": "data"}'
    signature = "sha256=invalid_signature"

    assert verify_signature(payload, signature, secret) is False


def test_verify_signature_wrong_format():
    """Test signature verification with wrong format."""
    secret = "test-secret"
    payload = b'{"test": "data"}'
    signature = "wrong_format"

    assert verify_signature(payload, signature, secret) is False


@pytest.fixture
def app():
    """Create test Flask app."""
    return create_webhook_app(webhook_secret="test-secret")


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


def test_health_endpoint(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "healthy"}


def test_webhook_ping(client):
    """Test webhook ping event."""
    payload = {"zen": "Design for failure."}
    payload_bytes = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"test-secret", payload_bytes, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhooks/github",
        data=payload_bytes,
        headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json == {"message": "pong"}


def test_webhook_invalid_signature(client):
    """Test webhook with invalid signature."""
    payload = {"test": "data"}
    payload_bytes = json.dumps(payload).encode()

    response = client.post(
        "/webhooks/github",
        data=payload_bytes,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": "sha256=invalid",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 401
    assert "Invalid signature" in response.json["error"]


def test_webhook_issue_labeled(client):
    """Test webhook for issue labeled event (no Temporal client)."""
    payload = {
        "action": "labeled",
        "issue": {
            "number": 123,
            "title": "Test issue",
            "body": "Test body",
            "user": {"login": "testuser"},
        },
        "label": {"name": "factory:build"},
        "repository": {
            "full_name": "test/repo",
            "clone_url": "https://github.com/test/repo.git",
            "default_branch": "main",
        },
    }
    payload_bytes = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"test-secret", payload_bytes, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhooks/github",
        data=payload_bytes,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json["status"] == "accepted"
    assert response.json["work_order_id"] == "WO-test-repo-123"


def test_webhook_issue_comment_factory_command(client):
    """Test webhook for /factory command in issue comment (no Temporal client)."""
    payload = {
        "action": "created",
        "issue": {
            "number": 456,
            "title": "Another test",
            "body": "Test description",
            "user": {"login": "testuser"},
        },
        "comment": {
            "body": "/factory build",
            "user": {"login": "testuser"},
            "author_association": "MEMBER",
        },
        "repository": {
            "full_name": "test/repo",
            "clone_url": "https://github.com/test/repo.git",
            "default_branch": "main",
        },
    }
    payload_bytes = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"test-secret", payload_bytes, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhooks/github",
        data=payload_bytes,
        headers={
            "X-GitHub-Event": "issue_comment",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json["status"] == "accepted"
    assert response.json["work_order_id"] == "WO-test-repo-456"


def test_webhook_unhandled_event(client):
    """Test webhook with unhandled event type."""
    payload = {"action": "opened"}
    payload_bytes = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"test-secret", payload_bytes, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhooks/github",
        data=payload_bytes,
        headers={
            "X-GitHub-Event": "repository",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json == {"message": "received"}


def test_webhook_with_temporal_client():
    """Test webhook with mocked Temporal client."""
    # Create mock Temporal client
    mock_client = MagicMock()
    mock_handle = MagicMock()
    mock_handle.first_execution_run_id = "test-run-id"
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)
    mock_client._task_queue = "test-queue"

    def temporal_client_factory():
        return mock_client

    app = create_webhook_app(
        temporal_client_factory=temporal_client_factory,
        webhook_secret="test-secret",
    )
    client = app.test_client()

    payload = {
        "action": "labeled",
        "issue": {
            "number": 789,
            "title": "Test with Temporal",
            "body": "Should start workflow",
            "user": {"login": "testuser"},
        },
        "label": {"name": "factory:build"},
        "repository": {
            "full_name": "test/repo",
            "clone_url": "https://github.com/test/repo.git",
            "default_branch": "main",
        },
    }
    payload_bytes = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"test-secret", payload_bytes, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhooks/github",
        data=payload_bytes,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json["status"] == "submitted"
    assert response.json["work_order_id"] == "WO-test-repo-789"
    assert response.json["workflow_id"] == "factory-WO-test-repo-789"
    assert "workflow_run_id" in response.json

    # Verify workflow was started
    mock_client.start_workflow.assert_called_once()
    call_args = mock_client.start_workflow.call_args
    assert call_args[0][0] == "execute_task_dag"
    assert call_args[1]["id"] == "factory-WO-test-repo-789"
    assert call_args[1]["task_queue"] == "test-queue"


def _post(client, event, payload):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/github",
        data=body,
        headers={
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )


def _comment(body, association="MEMBER", on_pr=False):
    issue = {"number": 7, "title": "t", "body": "b", "user": {"login": "u"}}
    if on_pr:
        issue["pull_request"] = {"url": "https://api.github.com/repos/test/repo/pulls/7"}
    return {
        "action": "created",
        "issue": issue,
        "comment": {"body": body, "user": {"login": "u"}, "author_association": association},
        "repository": {"full_name": "test/repo", "clone_url": "https://github.com/test/repo.git"},
    }


@pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR"])
def test_factory_command_from_outsider_is_ignored(client, association):
    response = _post(client, "issue_comment", _comment("/factory build", association))
    assert response.status_code == 200
    assert response.json["status"] == "ignored"


@pytest.mark.parametrize("body", ["/factoryx", "/factory destroy", "> /factory build", "hi"])
def test_only_exact_factory_commands_trigger(client, body):
    response = _post(client, "issue_comment", _comment(body))
    assert response.json == {"message": "received"}


def test_factory_command_on_pull_request_is_ignored(client):
    response = _post(client, "issue_comment", _comment("/factory", on_pr=True))
    assert response.json == {"message": "received"}


def test_command_with_trailing_text_on_later_lines_triggers(client):
    response = _post(client, "issue_comment", _comment("/factory build\nplease"))
    assert response.json["status"] == "accepted"


def test_receiver_refuses_to_start_without_secret():
    with pytest.raises(ValueError, match="webhook_secret is required"):
        create_webhook_app(webhook_secret=None)
    with pytest.raises(ValueError):
        create_webhook_app(webhook_secret="")


def test_non_json_body_rejected(client):
    body = b"not json"
    sig = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/github",
        data=body,
        headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": sig},
    )
    assert response.status_code == 400


def test_webhook_builds_a_valid_task_dag():
    from factory.schemas import TaskDAG

    mock_client = MagicMock()
    mock_client.start_workflow = AsyncMock(return_value=MagicMock(first_execution_run_id="r"))
    mock_client._task_queue = "q"
    app = create_webhook_app(
        temporal_client_factory=lambda: mock_client, webhook_secret="test-secret"
    )
    payload = _comment("/factory build")
    payload["issue"]["title"] = "x" * 300  # longer than Task.title allows
    payload["repository"]["default_branch"] = "develop"
    assert _post(app.test_client(), "issue_comment", payload).json["status"] == "submitted"

    dag = TaskDAG.model_validate(mock_client.start_workflow.call_args.kwargs["args"][0])
    task = dag.tasks[0]
    assert task.repo.url == "https://github.com/test/repo.git"
    assert task.repo.base_branch == "develop"
    assert len(task.title) == 200
