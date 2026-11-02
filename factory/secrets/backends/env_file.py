"""Environment variable and file-based secrets backend.

This backend is designed for local development and testing. It reads secrets
from environment variables or a .env-style file and mints "credentials" that
are actually just references to those static secrets.

NOT FOR PRODUCTION. Use VaultBackend or similar for real deployments.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from factory.secrets.broker import (
    Credential,
    MintRequest,
    SecretsBroker,
    SecretStatus,
)


class EnvFileBackend(SecretsBroker):
    """File-backed secrets broker for local/test environments.

    Secrets are loaded from:
    1. Environment variables (e.g. GITHUB_TOKEN, GITLAB_TOKEN)
    2. A .env file if provided
    3. A secrets.json file with structured mappings

    Minted credentials are stored in a JSON file and expired credentials
    are cleaned up on access.
    """

    def __init__(
        self,
        state_dir: Path,
        secrets_file: Path | None = None,
        env_file: Path | None = None,
    ):
        """Initialize the EnvFile backend.

        Args:
            state_dir: Directory to store minted credentials
            secrets_file: Optional JSON file with tool -> secret mappings
            env_file: Optional .env file to load
        """
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.credentials_file = self.state_dir / "credentials.json"
        self.secrets_file = secrets_file
        self.env_file = env_file

        self._lock = RLock()  # Use RLock for re-entrant locking
        self._credentials: dict[str, dict[str, Any]] = {}
        self._secrets_map: dict[str, str] = {}

        self._load_secrets()
        self._load_credentials()

    def _load_secrets(self) -> None:
        """Load secrets from files and environment."""
        # Load from secrets.json if provided
        if self.secrets_file and self.secrets_file.exists():
            data = json.loads(self.secrets_file.read_text())
            self._secrets_map.update(data.get("tools", {}))

        # Load from .env file if provided
        if self.env_file and self.env_file.exists():
            for line in self.env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    self._secrets_map[key.strip()] = value.strip().strip('"').strip("'")

        # Fall back to environment variables
        for key in ["GITHUB_TOKEN", "GITLAB_TOKEN", "BITBUCKET_TOKEN"]:
            if key in os.environ and key not in self._secrets_map:
                self._secrets_map[key] = os.environ[key]

    def _load_credentials(self) -> None:
        """Load existing credentials from disk."""
        if self.credentials_file.exists():
            try:
                self._credentials = json.loads(self.credentials_file.read_text())
            except (json.JSONDecodeError, OSError):
                self._credentials = {}

    def _save_credentials(self) -> None:
        """Persist credentials to disk."""
        self.credentials_file.write_text(json.dumps(self._credentials, indent=2))

    def _get_secret_for_tool(self, tool_name: str, repo_url: str) -> str:
        """Look up the appropriate secret for a tool/repo combination."""
        # Try tool-specific keys first
        if tool_name in self._secrets_map:
            return self._secrets_map[tool_name]

        # Try repo-specific inference
        if "github.com" in repo_url and "GITHUB_TOKEN" in self._secrets_map:
            return self._secrets_map["GITHUB_TOKEN"]
        if "gitlab.com" in repo_url and "GITLAB_TOKEN" in self._secrets_map:
            return self._secrets_map["GITLAB_TOKEN"]
        if "bitbucket.org" in repo_url and "BITBUCKET_TOKEN" in self._secrets_map:
            return self._secrets_map["BITBUCKET_TOKEN"]

        # Fallback: any available token
        if self._secrets_map:
            return next(iter(self._secrets_map.values()))

        raise ValueError(f"No secret available for tool '{tool_name}' and repo '{repo_url}'")

    def mint(self, request: MintRequest) -> Credential:
        """Mint a new credential by creating a reference to a static secret."""
        with self._lock:
            # Get the actual secret (but don't return it)
            secret_value = self._get_secret_for_tool(request.tool_name, request.repo_url)

            # Generate credential ID
            credential_id = f"cred_{secrets.token_hex(12)}"

            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(seconds=request.ttl_seconds)

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
                fingerprint=self.compute_fingerprint(secret_value),
                metadata={"reason": request.reason},
            )

            # Store credential metadata + the actual secret value
            self._credentials[credential_id] = {
                **credential.model_dump(mode="json"),
                "_secret_value": secret_value,  # Internal only, never exposed
            }

            self._save_credentials()
            return credential

    def get_credential(self, credential_id: str) -> Credential | None:
        """Get credential metadata without the secret value."""
        with self._lock:
            data = self._credentials.get(credential_id)
            if not data:
                return None

            # Remove secret value before constructing Credential
            data_copy = {k: v for k, v in data.items() if k != "_secret_value"}
            cred = Credential.model_validate(data_copy)

            # Auto-expire if needed
            if self.is_expired(cred) and cred.status == SecretStatus.ACTIVE:
                self._credentials[credential_id]["status"] = SecretStatus.EXPIRED.value
                self._save_credentials()
                cred.status = SecretStatus.EXPIRED

            return cred

    def inject_credential(self, credential_id: str, env: dict[str, str]) -> dict[str, str]:
        """Inject the secret value into the environment dict."""
        with self._lock:
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

            # Inject the secret into the environment
            secret_value = data["_secret_value"]

            # Use tool-specific environment variable names
            env_var_name = self._env_var_for_tool(cred.tool_name)
            env[env_var_name] = secret_value

            # Also inject credential ID for audit trail
            env["FACTORY_CREDENTIAL_ID"] = credential_id

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
        """Revoke a credential."""
        with self._lock:
            if credential_id not in self._credentials:
                return False

            cred_data = self._credentials[credential_id]
            if cred_data["status"] in (SecretStatus.EXPIRED.value, SecretStatus.REVOKED.value):
                return False

            cred_data["status"] = SecretStatus.REVOKED.value
            cred_data["revoked_at"] = datetime.now(timezone.utc).isoformat()
            self._save_credentials()
            return True

    def list_credentials(
        self, task_id: str | None = None, status: SecretStatus | None = None
    ) -> list[Credential]:
        """List credentials matching the filters."""
        with self._lock:
            results = []
            for data in self._credentials.values():
                data_copy = {k: v for k, v in data.items() if k != "_secret_value"}
                cred = Credential.model_validate(data_copy)

                # Auto-expire if needed
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
        """Remove expired credentials from storage."""
        with self._lock:
            expired_ids = []
            for cred_id, data in self._credentials.items():
                data_copy = {k: v for k, v in data.items() if k != "_secret_value"}
                cred = Credential.model_validate(data_copy)
                if self.is_expired(cred):
                    expired_ids.append(cred_id)

            for cred_id in expired_ids:
                del self._credentials[cred_id]

            if expired_ids:
                self._save_credentials()

            return len(expired_ids)
