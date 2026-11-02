"""GitHub App integration for the Software Factory.

This module provides the webhook receiver and GitHub API client for Phase 2a.
Work Orders are submitted to Temporal when issues are labeled or commands are
issued in comments.
"""

from github_app.client import GitHubAppClient
from github_app.receiver import create_webhook_app

__all__ = ["create_webhook_app", "GitHubAppClient"]
