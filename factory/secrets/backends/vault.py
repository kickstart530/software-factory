"""HashiCorp Vault backend for Secrets Broker.

This backend integrates with HashiCorp Vault via the CLI (`vault` command).
It uses Vault's dynamic secrets capabilities to mint short-lived credentials.

For production use, configure:
- VAULT_ADDR: Vault server URL
- VAULT_TOKEN: Vault authentication token (or use other auth methods)
- Vault policies that allow creating dynamic credentials

This is a documented adapter interface with CLI-oriented stubs.
Full implementation depends on specific Vault secret engines and policies.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from factory.harness.hooks import register_minted_secret
from factory.secrets.broker import (
    Credential,
    MintRequest,
    SecretsBroker,
    SecretStatus,
    read_credential_state,
    write_credential_state,
)


class VaultBackend(SecretsBroker):
    """Vault CLI-based secrets broker for production environments.

    This backend wraps the Vault CLI to:
    1. Mint dynamic credentials via secret engines (AWS, GCP, GitHub, etc.)
    2. Store credential metadata locally for tracking
    3. Revoke credentials before expiry via lease revocation

    Configuration:
        - state_dir: Local directory for credential metadata tracking
        - vault_path: Base path in Vault for credential operations
                      (e.g. "github/token" for GitHub dynamic tokens)
        - vault_role: Role to use when requesting credentials

    Prerequisites:
        - `vault` CLI installed and in PATH
        - VAULT_ADDR and VAULT_TOKEN environment variables set
        - Vault policies allowing read on {vault_path}/{vault_role}
    """

    def __init__(
        self,
        state_dir: Path,
        vault_path: str = "secret/factory",
        vault_role: str = "agent",
    ):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.vault_path = vault_path
        self.vault_role = vault_role
        self.credentials_file = self.state_dir / "vault_credentials.json"

        self._credentials: dict[str, dict[str, Any]] = {}
        self._load_credentials()

    def _load_credentials(self) -> None:
        """Load credential metadata; tokens are never read from disk."""
        self._credentials, leaked = read_credential_state(self.credentials_file)
        if leaked:  # migrate state written by older versions that stored tokens
            self._save_credentials()

    def _save_credentials(self) -> None:
        """Persist metadata only (mode 0600); Vault tokens stay in memory."""
        write_credential_state(self.credentials_file, self._credentials)

    def _vault_read(self, path: str) -> dict[str, Any]:
        """Execute `vault read -format=json <path>` and return parsed result.

        Raises:
            RuntimeError: If vault command fails
        """
        result = subprocess.run(
            ["vault", "read", "-format=json", path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"vault read failed: {result.stderr}")

        return json.loads(result.stdout)

    def _vault_write(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        """Execute `vault write -format=json <path> <data>` and return parsed result.

        Raises:
            RuntimeError: If vault command fails
        """
        args = ["vault", "write", "-format=json", path]
        for key, value in data.items():
            args.append(f"{key}={value}")

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"vault write failed: {result.stderr}")

        return json.loads(result.stdout)

    def _vault_revoke(self, lease_id: str) -> bool:
        """Execute `vault lease revoke <lease_id>`.

        Returns:
            True if revocation succeeded, False otherwise
        """
        result = subprocess.run(
            ["vault", "lease", "revoke", lease_id],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def mint(self, request: MintRequest) -> Credential:
        """Mint a credential by requesting a dynamic secret from Vault.

        This implementation requests a GitHub token. Adapt for other secret engines:
        - AWS: aws/creds/{role}
        - GCP: gcp/token/{roleset}
        - Database: database/creds/{role}
        """
        credential_id = f"cred_{secrets.token_hex(12)}"

        # Build Vault request path
        # Example: github/token/{vault_role} for GitHub App tokens
        # Adapt this based on your Vault secret engine configuration
        vault_request_path = f"{self.vault_path}/{self.vault_role}"

        try:
            # Request credential from Vault with metadata
            response = self._vault_write(
                vault_request_path,
                {
                    "ttl": f"{request.ttl_seconds}s",
                    "metadata": json.dumps(
                        {
                            "task_id": request.task_id,
                            "work_order_id": request.work_order_id,
                            "repo_url": request.repo_url,
                        }
                    ),
                },
            )

            # Extract lease information
            lease_id = response.get("lease_id", "")
            lease_duration = response.get("lease_duration", request.ttl_seconds)

            # Extract the actual token from response
            # Path depends on secret engine; for GitHub: data.token
            secret_data = response.get("data", {})
            token_value = secret_data.get("token", "")

            if not token_value:
                raise ValueError("Vault did not return a token")

            now = datetime.now(UTC)
            expires_at = now + timedelta(seconds=lease_duration)

            credential = Credential(
                credential_id=credential_id,
                task_id=request.task_id,
                work_order_id=request.work_order_id,
                repo_url=request.repo_url,
                branch=request.branch,
                tool_name=request.tool_name,
                scopes=request.scopes,
                status=SecretStatus.ACTIVE,
                minted_at=now,
                expires_at=expires_at,
                fingerprint=self.compute_fingerprint(token_value),
                metadata={
                    "reason": request.reason,
                    "vault_lease_id": lease_id,
                    "vault_path": vault_request_path,
                },
            )

            # Store credential metadata + secret value locally
            self._credentials[credential_id] = {
                **credential.model_dump(mode="json"),
                "_secret_value": token_value,
                "_vault_lease_id": lease_id,
            }
            self._save_credentials()
            register_minted_secret(token_value)

            return credential

        except Exception as e:
            raise RuntimeError(f"Failed to mint credential from Vault: {e}") from e

    def get_credential(self, credential_id: str) -> Credential | None:
        """Get credential metadata without the secret value."""
        data = self._credentials.get(credential_id)
        if not data:
            return None

        data_copy = {k: v for k, v in data.items() if not k.startswith("_")}
        cred = Credential.model_validate(data_copy)

        # Auto-expire if needed
        if self.is_expired(cred) and cred.status == SecretStatus.ACTIVE:
            self._credentials[credential_id]["status"] = SecretStatus.EXPIRED.value
            self._save_credentials()
            cred.status = SecretStatus.EXPIRED

        return cred

    def inject_credential(self, credential_id: str, env: dict[str, str]) -> dict[str, str]:
        """Inject the Vault-minted secret into the environment."""
        data = self._credentials.get(credential_id)
        if not data:
            raise ValueError(f"Credential {credential_id} not found")

        cred = self.get_credential(credential_id)
        if not cred:
            raise ValueError(f"Credential {credential_id} not found")

        if cred.status == SecretStatus.EXPIRED:
            raise ValueError(f"Credential {credential_id} has expired")
        if cred.status == SecretStatus.REVOKED:
            raise ValueError(f"Credential {credential_id} has been revoked")

        secret_value = data.get("_secret_value")
        if secret_value is None:
            raise ValueError(
                f"Credential {credential_id} was minted by an earlier process and its token "
                "is not held here; mint a new credential"
            )
        env_var_name = self._env_var_for_tool(cred.tool_name)
        env[env_var_name] = secret_value
        env["FACTORY_CREDENTIAL_ID"] = credential_id

        # Also inject Vault lease ID for audit
        if "_vault_lease_id" in data:
            env["VAULT_LEASE_ID"] = data["_vault_lease_id"]

        return env

    def _env_var_for_tool(self, tool_name: str) -> str:
        """Map tool name to environment variable name."""
        mapping = {
            "gh": "GITHUB_TOKEN",
            "git": "GIT_TOKEN",
            "gitlab": "GITLAB_TOKEN",
            "bitbucket": "BITBUCKET_TOKEN",
        }
        return mapping.get(tool_name, f"{tool_name.upper()}_TOKEN")

    def revoke(self, credential_id: str) -> bool:
        """Revoke a credential via Vault lease revocation."""
        if credential_id not in self._credentials:
            return False

        cred_data = self._credentials[credential_id]
        if cred_data["status"] in (SecretStatus.EXPIRED.value, SecretStatus.REVOKED.value):
            return False

        # Revoke the Vault lease if present
        lease_id = cred_data.get("_vault_lease_id")
        if lease_id:
            with contextlib.suppress(Exception):
                # Continue even if Vault revoke fails (lease will expire naturally)
                self._vault_revoke(lease_id)

        # Mark as revoked locally
        cred_data["status"] = SecretStatus.REVOKED.value
        cred_data["revoked_at"] = datetime.now(UTC).isoformat()
        self._save_credentials()
        return True

    def list_credentials(
        self, task_id: str | None = None, status: SecretStatus | None = None
    ) -> list[Credential]:
        """List credentials matching the filters."""
        results = []
        for data in self._credentials.values():
            data_copy = {k: v for k, v in data.items() if not k.startswith("_")}
            cred = Credential.model_validate(data_copy)

            # Auto-expire
            if self.is_expired(cred) and cred.status == SecretStatus.ACTIVE:
                cred.status = SecretStatus.EXPIRED

            # Apply filters
            if task_id and cred.task_id != task_id:
                continue
            if status and cred.status != status:
                continue

            results.append(cred)

        return results

    def cleanup_expired(self) -> int:
        """Remove expired credentials from local storage."""
        expired_ids = []
        for cred_id, data in self._credentials.items():
            data_copy = {k: v for k, v in data.items() if not k.startswith("_")}
            cred = Credential.model_validate(data_copy)
            if self.is_expired(cred):
                expired_ids.append(cred_id)

        for cred_id in expired_ids:
            del self._credentials[cred_id]

        if expired_ids:
            self._save_credentials()

        return len(expired_ids)
