# GitHub App Setup — Phase 2a

This document describes how to install and configure the Software Factory GitHub App for one pilot repository.

## Overview

The GitHub App receives webhook events (issues, PRs, CI status) and creates Work Orders that will be submitted to Temporal workflows. Phase 2a provides the webhook receiver and API client; Temporal workflow invocation is integration work.

## Creating the GitHub App

### Option 1: Using the Manifest (Recommended)

1. Go to your organization settings: `https://github.com/organizations/YOUR_ORG/settings/apps`
2. Click **"New GitHub App"**
3. Use the manifest at `github_app/manifest.yml` or manually configure:

**Basic Information:**
- **Name:** Software Factory
- **Homepage URL:** `https://github.com/kickstart530/software-factory`
- **Webhook URL:** `https://YOUR_DOMAIN/webhooks/github`
  - For local dev: use ngrok or similar tunnel
- **Webhook Secret:** Generate a secure random secret (save it for configuration)

**Permissions (Repository level):**
- Contents: Read & Write (push branches, read code)
- Pull Requests: Read & Write (create/update PRs)
- Issues: Read (read issue details for Work Orders)
- Metadata: Read (required baseline)
- Workflows: Read (check CI status)
- Commit statuses: Read (check build status)

**Subscribe to Events:**
- issues
- issue_comment
- pull_request
- pull_request_review
- pull_request_review_comment
- check_run
- workflow_run
- status

**Where can this app be installed:** Only on this account (private app)

4. After creation, note the **App ID** and generate a **private key** (PEM file)

### Option 2: Manual Configuration

Follow the same settings as above, configuring each field manually in the GitHub UI.

## Installing on a Pilot Repository

1. Go to app settings: `https://github.com/organizations/YOUR_ORG/settings/apps/YOUR_APP`
2. Click **"Install App"**
3. Select **"Only select repositories"**
4. Choose your pilot repository (e.g., `kickstart530/software-factory`)
5. Click **"Install"**
6. Note the **Installation ID** from the URL after installation

## Configuring the Webhook Receiver

The webhook receiver needs three pieces of configuration:

```bash
# Environment variables for the webhook receiver
export GITHUB_APP_ID="123456"
export GITHUB_APP_PRIVATE_KEY_PATH="/path/to/private-key.pem"
export GITHUB_APP_INSTALLATION_ID="12345678"
export GITHUB_WEBHOOK_SECRET="your-webhook-secret"
export TEMPORAL_HOST="localhost:7233"  # Temporal server address
```

### Local Development with ngrok

For local testing:

```bash
# Start ngrok tunnel
ngrok http 8080

# Note the public URL (e.g., https://abc123.ngrok.io)
# Update GitHub App webhook URL to: https://abc123.ngrok.io/webhooks/github

# Run the webhook receiver
python -m github_app.server
```

### Production Deployment

Deploy options:
1. **Kubernetes:** Deploy as a service with ingress (see `deploy/github-app/` when available)
2. **Cloud Run / App Engine:** Serverless deployment for low traffic
3. **VM with systemd:** Traditional deployment with reverse proxy

## Using the GitHub App

### Triggering Work Orders

**Method 1: Label an issue**
1. Create or open an issue in the pilot repository
2. Add the label `factory:build`
3. The webhook receiver creates a Work Order from the issue

**Method 2: Comment command**
1. Create or open an issue
2. Comment: `/factory build`
3. The webhook receiver creates a Work Order

### What Happens

Phase 2a webhook receiver:
1. Receives the webhook event
2. Validates the signature
3. Parses the issue title/body into a Work Order structure
4. Logs the Work Order (stubbed: Temporal invocation is integration work)
5. Responds with HTTP 200

**Note:** Work Order → Temporal workflow invocation is not implemented in Phase 2a. The webhook receiver creates the Work Order structure but doesn't yet submit it to Temporal. That's integration work after sandbox/cluster streams complete their components.

## Webhook Payloads

### Issue Labeled Event

```json
{
  "action": "labeled",
  "issue": {
    "number": 123,
    "title": "Add user authentication",
    "body": "Implement OAuth2 login...",
    "user": {"login": "alice"}
  },
  "label": {"name": "factory:build"},
  "repository": {
    "full_name": "kickstart530/software-factory",
    "clone_url": "https://github.com/kickstart530/software-factory.git",
    "default_branch": "main"
  }
}
```

### Issue Comment Event

```json
{
  "action": "created",
  "issue": {
    "number": 123,
    "title": "Add user authentication",
    "body": "Implement OAuth2 login..."
  },
  "comment": {
    "body": "/factory build",
    "user": {"login": "alice"}
  },
  "repository": { ... }
}
```

## API Client Usage

The `GitHubAppClient` provides authenticated API access for creating branches and PRs:

```python
from github_app.client import GitHubAppClient

# Initialize client
client = GitHubAppClient(
    app_id="123456", private_key=open("private-key.pem").read(), installation_id="12345678"
)

# Create a branch for a task
client.create_branch("kickstart530", "software-factory", "factory/task-123", "main")

# Create a PR when task completes
pr = client.create_pull_request(
    "kickstart530",
    "software-factory",
    title="Implement user authentication",
    head="factory/task-123",
    base="main",
    body="Closes #123\n\n## Changes\n- Added OAuth2 login\n- Added tests",
)
```

## Security Considerations

- **Webhook Secret:** Store securely (Kubernetes Secret, Vault, etc.)
- **Private Key:** Never commit to git; mount as secret in production
- **Installation Token:** Client auto-refreshes tokens (1-hour lifetime)
- **Signature Verification:** All webhook requests are verified before processing
- **Least Privilege:** App has minimal permissions needed for Phase 2a

## Troubleshooting

### Webhook Not Received

1. Check webhook URL is publicly accessible
2. Verify webhook secret matches configuration
3. Check GitHub App delivery tab for errors
4. Review webhook receiver logs

### Authentication Failed

1. Verify App ID and Installation ID are correct
2. Check private key format (PEM, not DER)
3. Ensure clock is synchronized (JWT uses timestamps)

### PR Creation Failed

1. Check app has "Contents: Write" permission
2. Verify base branch exists
3. Check for branch name conflicts

## Next Steps

After GitHub App is installed:
1. Test webhook delivery with a ping event
2. Trigger a Work Order via issue label
3. Monitor webhook receiver logs
4. Wait for Temporal workflow invocation integration (post-Phase 2a)

## Architecture Note

**Known Issue (P0):** The current `run_task_activity` in `factory/temporal_supervisor.py` expects a TaskRunner in heartbeat_details, which is always empty, causing tasks to fail. Stream B/C (sandbox/worker) own fixing this. The GitHub App webhook receiver is designed not to depend on this—it creates Work Order structures but doesn't invoke workflows directly. Temporal invocation is integration work after sandbox and cluster components are complete.
