"""Tests for Secrets Broker: minting, expiry, revocation, and redaction.

These tests verify that:
1. Credentials can be minted with task-scoped metadata
2. Secret values are NEVER exposed in Credential objects
3. Credentials expire correctly
4. Credentials can be revoked
5. Secrets are redacted from transcripts
6. Both EnvFile and Vault backends work correctly
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.secrets import (
    Credential,
    MintRequest,
    SecretStatus,
)
from factory.secrets.backends import EnvFileBackend, VaultBackend


class TestEnvFileBackend:
    """Test the environment/file-based secrets backend."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def secrets_file(self, temp_dir):
        secrets = {"tools": {"gh": "test_github_token_12345", "git": "test_git_token_67890"}}
        secrets_path = temp_dir / "secrets.json"
        secrets_path.write_text(json.dumps(secrets))
        return secrets_path

    @pytest.fixture
    def backend(self, temp_dir, secrets_file):
        return EnvFileBackend(state_dir=temp_dir, secrets_file=secrets_file)

    def test_mint_credential(self, backend):
        """Test minting a new credential."""
        request = MintRequest(
            task_id="task-1",
            work_order_id="wo-1",
            repo_url="https://github.com/example/repo",
            branch="main",
            tool_name="gh",
            scopes=["repo", "read:org"],
            ttl_seconds=3600,
            reason="Test credential",
        )

        credential = backend.mint(request)

        assert credential.task_id == "task-1"
        assert credential.work_order_id == "wo-1"
        assert credential.repo_url == "https://github.com/example/repo"
        assert credential.branch == "main"
        assert credential.tool_name == "gh"
        assert credential.scopes == ["repo", "read:org"]
        assert credential.status == SecretStatus.ACTIVE
        assert credential.fingerprint  # Should have a fingerprint
        assert credential.credential_id.startswith("cred_")

        # Verify expiry is approximately correct
        expected_expiry = datetime.now(timezone.utc) + timedelta(seconds=3600)
        assert abs((credential.expires_at - expected_expiry).total_seconds()) < 5

    def test_credential_never_exposes_secret(self, backend):
        """Critical: Verify secret value is NEVER in Credential object."""
        request = MintRequest(
            task_id="task-2",
            work_order_id="wo-2",
            repo_url="https://github.com/example/repo",
            tool_name="gh",
        )

        credential = backend.mint(request)

        # Check all fields of the Credential
        cred_dict = credential.model_dump()
        cred_json = credential.model_dump_json()

        # Secret value should not be in any field
        assert "test_github_token" not in cred_json
        assert "_secret_value" not in cred_dict

        # Verify get_credential also doesn't expose it
        retrieved = backend.get_credential(credential.credential_id)
        assert retrieved is not None
        retrieved_dict = retrieved.model_dump()
        retrieved_json = retrieved.model_dump_json()

        assert "test_github_token" not in retrieved_json
        assert "_secret_value" not in retrieved_dict

    def test_inject_credential_into_env(self, backend):
        """Test that credentials can be injected into environment for tool execution."""
        request = MintRequest(
            task_id="task-3",
            work_order_id="wo-3",
            repo_url="https://github.com/example/repo",
            tool_name="gh",
        )

        credential = backend.mint(request)

        # Inject into environment
        env = {}
        env_with_secret = backend.inject_credential(credential.credential_id, env)

        # Secret should now be in environment
        assert "GITHUB_TOKEN" in env_with_secret
        assert env_with_secret["GITHUB_TOKEN"] == "test_github_token_12345"
        assert env_with_secret["FACTORY_CREDENTIAL_ID"] == credential.credential_id

    def test_credential_expiry(self, backend):
        """Test that expired credentials cannot be used."""
        request = MintRequest(
            task_id="task-4",
            work_order_id="wo-4",
            repo_url="https://github.com/example/repo",
            tool_name="gh",
            ttl_seconds=1,  # Expire in 1 second
        )

        credential = backend.mint(request)
        assert credential.status == SecretStatus.ACTIVE

        # Wait for expiry
        time.sleep(1.5)

        # Credential should be expired when retrieved
        retrieved = backend.get_credential(credential.credential_id)
        assert retrieved is not None
        assert retrieved.status == SecretStatus.EXPIRED

        # Should not be able to inject expired credential
        with pytest.raises(ValueError, match="has expired"):
            backend.inject_credential(credential.credential_id, {})

    def test_revoke_credential(self, backend):
        """Test manual credential revocation."""
        request = MintRequest(
            task_id="task-5",
            work_order_id="wo-5",
            repo_url="https://github.com/example/repo",
            tool_name="gh",
        )

        credential = backend.mint(request)
        assert credential.status == SecretStatus.ACTIVE

        # Revoke the credential
        assert backend.revoke(credential.credential_id)

        # Should be marked as revoked
        retrieved = backend.get_credential(credential.credential_id)
        assert retrieved is not None
        assert retrieved.status == SecretStatus.REVOKED

        # Should not be able to inject revoked credential
        with pytest.raises(ValueError, match="has been revoked"):
            backend.inject_credential(credential.credential_id, {})

        # Cannot revoke twice
        assert not backend.revoke(credential.credential_id)

    def test_list_credentials_filtering(self, backend):
        """Test listing credentials with filters."""
        # Create multiple credentials
        for i in range(3):
            request = MintRequest(
                task_id=f"task-{i}",
                work_order_id="wo-6",
                repo_url="https://github.com/example/repo",
                tool_name="gh",
            )
            backend.mint(request)

        # List all
        all_creds = backend.list_credentials()
        assert len(all_creds) == 3

        # Filter by task_id
        task_creds = backend.list_credentials(task_id="task-0")
        assert len(task_creds) == 1
        assert task_creds[0].task_id == "task-0"

        # Filter by status
        backend.revoke(all_creds[0].credential_id)
        revoked_creds = backend.list_credentials(status=SecretStatus.REVOKED)
        assert len(revoked_creds) == 1

        active_creds = backend.list_credentials(status=SecretStatus.ACTIVE)
        assert len(active_creds) == 2

    def test_cleanup_expired(self, backend):
        """Test cleanup of expired credentials."""
        # Create short-lived credentials
        for i in range(3):
            request = MintRequest(
                task_id=f"task-{i}",
                work_order_id="wo-7",
                repo_url="https://github.com/example/repo",
                tool_name="gh",
                ttl_seconds=1,
            )
            backend.mint(request)

        # Wait for expiry
        time.sleep(1.5)

        # Cleanup
        count = backend.cleanup_expired()
        assert count == 3

        # Should be no credentials left
        all_creds = backend.list_credentials()
        assert len(all_creds) == 0

    def test_persistence_across_instances(self, temp_dir, secrets_file):
        """Test that credentials persist across backend instances."""
        backend1 = EnvFileBackend(state_dir=temp_dir, secrets_file=secrets_file)

        request = MintRequest(
            task_id="task-persist",
            work_order_id="wo-persist",
            repo_url="https://github.com/example/repo",
            tool_name="gh",
        )
        cred1 = backend1.mint(request)

        # Create new instance
        backend2 = EnvFileBackend(state_dir=temp_dir, secrets_file=secrets_file)

        # Should be able to retrieve the credential
        cred2 = backend2.get_credential(cred1.credential_id)
        assert cred2 is not None
        assert cred2.credential_id == cred1.credential_id
        assert cred2.task_id == "task-persist"

        # Should still be able to inject it
        env = backend2.inject_credential(cred1.credential_id, {})
        assert "GITHUB_TOKEN" in env

    def test_env_var_fallback(self, temp_dir):
        """Test fallback to environment variables when no secrets file."""
        with patch.dict(os.environ, {"GITHUB_TOKEN": "env_github_token"}):
            backend = EnvFileBackend(state_dir=temp_dir)

            request = MintRequest(
                task_id="task-env",
                work_order_id="wo-env",
                repo_url="https://github.com/example/repo",
                tool_name="gh",
            )
            credential = backend.mint(request)

            # Should use env var
            env = backend.inject_credential(credential.credential_id, {})
            assert env["GITHUB_TOKEN"] == "env_github_token"


class TestVaultBackend:
    """Test the Vault CLI-based backend (with mocked vault commands)."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def backend(self, temp_dir):
        return VaultBackend(
            state_dir=temp_dir,
            vault_path="github/token",
            vault_role="agent",
        )

    @patch("factory.secrets.backends.vault.subprocess.run")
    def test_mint_credential_via_vault(self, mock_run, backend):
        """Test minting a credential via Vault CLI."""
        # Mock successful vault write response
        mock_response = {
            "lease_id": "github/token/agent/abc123",
            "lease_duration": 3600,
            "data": {"token": "vault_generated_token_xyz"},
        }
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(mock_response),
        )

        request = MintRequest(
            task_id="task-vault",
            work_order_id="wo-vault",
            repo_url="https://github.com/example/repo",
            tool_name="gh",
            ttl_seconds=3600,
        )

        credential = backend.mint(request)

        # Verify vault write was called
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "vault"
        assert call_args[1] == "write"

        # Verify credential metadata
        assert credential.task_id == "task-vault"
        assert credential.status == SecretStatus.ACTIVE
        assert credential.metadata["vault_lease_id"] == "github/token/agent/abc123"

        # Secret should not be in credential
        assert "vault_generated_token" not in credential.model_dump_json()

    @patch("factory.secrets.backends.vault.subprocess.run")
    def test_inject_vault_credential(self, mock_run, backend):
        """Test injecting a Vault-minted credential into environment."""
        mock_response = {
            "lease_id": "github/token/agent/def456",
            "lease_duration": 3600,
            "data": {"token": "vault_secret_123"},
        }
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(mock_response),
        )

        request = MintRequest(
            task_id="task-inject",
            work_order_id="wo-inject",
            repo_url="https://github.com/example/repo",
            tool_name="gh",
        )

        credential = backend.mint(request)

        # Inject into environment
        env = backend.inject_credential(credential.credential_id, {})

        assert env["GITHUB_TOKEN"] == "vault_secret_123"
        assert env["FACTORY_CREDENTIAL_ID"] == credential.credential_id
        assert env["VAULT_LEASE_ID"] == "github/token/agent/def456"

    @patch("factory.secrets.backends.vault.subprocess.run")
    def test_revoke_vault_credential(self, mock_run, backend):
        """Test revoking a Vault credential."""
        # Mock mint
        mint_response = {
            "lease_id": "github/token/agent/revoke789",
            "lease_duration": 3600,
            "data": {"token": "vault_to_revoke"},
        }
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(mint_response),
        )

        request = MintRequest(
            task_id="task-revoke-vault",
            work_order_id="wo-revoke-vault",
            repo_url="https://github.com/example/repo",
            tool_name="gh",
        )

        credential = backend.mint(request)

        # Mock revoke
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Revoke
        assert backend.revoke(credential.credential_id)

        # Verify vault lease revoke was called
        revoke_call = [c for c in mock_run.call_args_list if "lease" in str(c)]
        assert len(revoke_call) > 0

        # Credential should be revoked
        retrieved = backend.get_credential(credential.credential_id)
        assert retrieved is not None
        assert retrieved.status == SecretStatus.REVOKED

    @patch("factory.secrets.backends.vault.subprocess.run")
    def test_vault_error_handling(self, mock_run, backend):
        """Test handling of Vault CLI errors."""
        # Mock failed vault write
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="Error: permission denied",
        )

        request = MintRequest(
            task_id="task-fail",
            work_order_id="wo-fail",
            repo_url="https://github.com/example/repo",
            tool_name="gh",
        )

        # Should raise RuntimeError
        with pytest.raises(RuntimeError, match="Failed to mint credential from Vault"):
            backend.mint(request)


class TestSecretsRedaction:
    """Test that secrets are properly redacted from transcripts."""

    def test_redact_credential_ids(self):
        """Test that credential IDs are redacted from text."""
        from factory.harness.hooks import find_secrets, redact_secrets

        text_with_cred = "Using credential cred_abc123def456789012345678 for authentication"

        # Should detect the credential ID
        found = find_secrets(text_with_cred)
        assert "minted-credential-id" in found

        # Should redact it
        redacted = redact_secrets(text_with_cred)
        assert "cred_abc123def456789012345678" not in redacted
        assert "[REDACTED:minted-credential-id]" in redacted

    def test_secret_write_guard_blocks_credentials(self):
        """Test that the write guard prevents writing credential IDs."""
        from factory.harness.hooks import secret_write_guard
        from factory.harness.tools import Tool, ToolInput

        # Create a minimal tool for testing
        tool = Tool(
            name="write_file",
            description="Write a file",
            input_model=ToolInput,
            handler=lambda ctx, inp: "ok",
            mutates=True,
        )

        args = {
            "path": "config.yaml",
            "content": "token: cred_abcdef123456789012345678\n",
        }

        decision = secret_write_guard(tool, args)
        assert decision is not None
        assert not decision.allowed
        assert "minted-credential-id" in decision.reason


class TestSecretsBrokerIntegration:
    """Integration tests proving model context never receives secrets."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def backend(self, temp_dir):
        secrets = {"tools": {"gh": "super_secret_token_do_not_expose"}}
        secrets_path = temp_dir / "secrets.json"
        secrets_path.write_text(json.dumps(secrets))
        return EnvFileBackend(state_dir=temp_dir, secrets_file=secrets_path)

    def test_end_to_end_secret_never_in_context(self, backend):
        """Prove that a minted secret never appears in model-visible context.

        This is the critical security property of the Secrets Broker.
        """
        request = MintRequest(
            task_id="task-e2e",
            work_order_id="wo-e2e",
            repo_url="https://github.com/example/repo",
            tool_name="gh",
        )

        # Step 1: Mint credential
        credential = backend.mint(request)

        # Step 2: Serialize credential (as would be sent to model)
        credential_json = credential.model_dump_json()

        # CRITICAL: Secret value must NOT be in the serialized credential
        assert "super_secret_token" not in credential_json
        assert "_secret_value" not in credential_json

        # Step 3: List credentials (as would be shown to model)
        all_creds = backend.list_credentials()
        all_creds_json = json.dumps([c.model_dump(mode="json") for c in all_creds])

        # CRITICAL: Secret must NOT be in the list
        assert "super_secret_token" not in all_creds_json

        # Step 4: Get credential status (as would be shown to model)
        status = backend.get_credential(credential.credential_id)
        assert status is not None
        status_json = status.model_dump_json()

        # CRITICAL: Secret must NOT be in status
        assert "super_secret_token" not in status_json

        # Step 5: Inject credential (server-side only, not visible to model)
        env = backend.inject_credential(credential.credential_id, {})

        # The secret IS in the environment (for tool execution)
        assert env["GITHUB_TOKEN"] == "super_secret_token_do_not_expose"

        # But if we simulate tool output containing the credential ID...
        from factory.harness.hooks import redact_secrets

        fake_tool_output = f"Using credential {credential.credential_id} for authentication"
        redacted_output = redact_secrets(fake_tool_output)

        # The credential ID should be redacted (preventing correlation attacks)
        assert credential.credential_id not in redacted_output
        assert "[REDACTED:minted-credential-id]" in redacted_output

        # Conclusion: Secret never appears in model-visible context,
        # only in server-side environment for tool execution.
        # The security model relies on:
        # 1. Credentials never returned in Credential objects (proven above)
        # 2. Credentials only injected server-side into tool environments
        # 3. Credential IDs redacted from transcripts to prevent correlation
        # 4. Tool outputs should not echo environment variables
        #
        # Note: If a tool explicitly echoes its environment variable values
        # (bad practice), that's a tool implementation bug, not a broker bug.
        # The broker's job is to never expose secrets in its API surface.
