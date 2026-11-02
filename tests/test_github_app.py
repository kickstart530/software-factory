"""Tests for GitHub App webhook receiver and API client."""

import hashlib
import hmac
import json

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
    """Test webhook for issue labeled event."""
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
    assert response.json["status"] == "submitted"
    assert response.json["work_order_id"] == "WO-test-repo-123"


def test_webhook_issue_comment_factory_command(client):
    """Test webhook for /factory command in issue comment."""
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
    assert response.json["status"] == "submitted"
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
