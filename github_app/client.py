"""GitHub API client for the Software Factory GitHub App.

Provides authenticated API access for creating branches, opening PRs, and
reading repository contents.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import requests


class GitHubAppClient:
    """Client for GitHub API using GitHub App authentication.

    Phase 2a: supports branch creation, PR operations, and repo access.
    Uses PyJWT for App token generation and requests for API calls.
    """

    def __init__(
        self,
        app_id: str,
        private_key: str,
        installation_id: str | None = None,
    ):
        """Initialize GitHub App client.

        Args:
            app_id: GitHub App ID
            private_key: PEM-encoded private key for the app
            installation_id: Optional installation ID (can be set later)
        """
        self.app_id = app_id
        self.private_key = private_key
        self.installation_id = installation_id
        self._installation_token: str | None = None
        self._token_expires_at: float = 0

    def set_installation(self, installation_id: str) -> None:
        """Set the installation ID for API calls."""
        self.installation_id = installation_id
        self._installation_token = None  # Invalidate cached token

    def _generate_jwt(self) -> str:
        """Generate a JWT for GitHub App authentication."""
        now = int(time.time())
        payload = {
            "iat": now - 60,  # Issued 60 seconds in the past (clock skew)
            "exp": now + (10 * 60),  # Expires in 10 minutes
            "iss": self.app_id,
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    def _get_installation_token(self) -> str:
        """Get or refresh an installation access token."""
        if not self.installation_id:
            raise ValueError("installation_id not set")

        # Return cached token if still valid (with 5 min buffer)
        if self._installation_token and time.time() < (self._token_expires_at - 300):
            return self._installation_token

        # Request new installation token
        jwt_token = self._generate_jwt()
        url = f"https://api.github.com/app/installations/{self.installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
        }

        response = requests.post(url, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()
        self._installation_token = data["token"]
        # GitHub tokens expire in 1 hour
        self._token_expires_at = time.time() + 3600

        return self._installation_token

    def _api_call(self, method: str, path: str, **kwargs) -> Any:
        """Make an authenticated GitHub API call.

        Args:
            method: HTTP method (GET, POST, PATCH, etc.)
            path: API path (without /api/v3 prefix)
            **kwargs: Additional arguments passed to requests

        Returns:
            Response JSON
        """
        token = self._get_installation_token()
        url = f"https://api.github.com{path}"
        headers = kwargs.pop("headers", {})
        headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            }
        )

        response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        response.raise_for_status()

        return response.json() if response.content else None

    def create_branch(self, owner: str, repo: str, branch: str, from_ref: str = "main") -> dict:
        """Create a new branch.

        Args:
            owner: Repository owner
            repo: Repository name
            branch: New branch name
            from_ref: Base branch or commit SHA

        Returns:
            API response with branch details
        """
        # Get the SHA of the base ref
        ref_data = self._api_call("GET", f"/repos/{owner}/{repo}/git/ref/heads/{from_ref}")
        sha = ref_data["object"]["sha"]

        # Create the new branch
        return self._api_call(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
    ) -> dict:
        """Create a pull request.

        Args:
            owner: Repository owner
            repo: Repository name
            title: PR title
            head: Head branch (where changes are)
            base: Base branch (where changes will merge)
            body: PR description
            draft: Whether to create as draft

        Returns:
            API response with PR details
        """
        return self._api_call(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
                "draft": draft,
            },
        )

    def update_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
    ) -> dict:
        """Update a pull request.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: PR number
            title: New title (optional)
            body: New body (optional)
            state: New state: "open" or "closed" (optional)

        Returns:
            API response with updated PR details
        """
        data = {}
        if title is not None:
            data["title"] = title
        if body is not None:
            data["body"] = body
        if state is not None:
            data["state"] = state

        return self._api_call("PATCH", f"/repos/{owner}/{repo}/pulls/{pr_number}", json=data)

    def get_pull_request(self, owner: str, repo: str, pr_number: int) -> dict:
        """Get pull request details.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: PR number

        Returns:
            PR details
        """
        return self._api_call("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}")

    def get_repository(self, owner: str, repo: str) -> dict:
        """Get repository details.

        Args:
            owner: Repository owner
            repo: Repository name

        Returns:
            Repository details
        """
        return self._api_call("GET", f"/repos/{owner}/{repo}")

    def add_comment(self, owner: str, repo: str, issue_number: int, body: str) -> dict:
        """Add a comment to an issue or PR.

        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue or PR number
            body: Comment text

        Returns:
            API response with comment details
        """
        return self._api_call(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )

    def list_pull_request_files(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """List files changed in a pull request.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: PR number

        Returns:
            List of file change details
        """
        return self._api_call("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}/files")

    def get_commit_status(self, owner: str, repo: str, ref: str) -> dict:
        """Get combined status for a commit.

        Args:
            owner: Repository owner
            repo: Repository name
            ref: Commit SHA, branch, or tag

        Returns:
            Combined status details
        """
        return self._api_call("GET", f"/repos/{owner}/{repo}/commits/{ref}/status")
