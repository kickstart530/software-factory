# GitHub App Setup — Phase 2a Integration

This document describes how to install and configure the Software Factory GitHub App for one pilot repository, including the end-to-end integration with Temporal workflows.

## Overview

The GitHub App receives webhook events (issues, PRs, CI status) and creates Work Orders that are submitted to Temporal workflows. Phase 2a completes the webhook receiver → Temporal workflow integration.

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

The webhook receiver needs these environment variables:

```bash
# GitHub App credentials
export GITHUB_APP_ID="123456"
export GITHUB_APP_PRIVATE_KEY_PATH="/path/to/private-key.pem"
export GITHUB_APP_INSTALLATION_ID="12345678"
export GITHUB_WEBHOOK_SECRET="your-webhook-secret"  # required: the receiver won't start without it

# Temporal configuration
export TEMPORAL_HOST="localhost:7233"  # Temporal server address
export TEMPORAL_NAMESPACE="default"    # Temporal namespace (optional)
export TEMPORAL_TASK_QUEUE="factory-tasks"  # Task queue name (optional)

# Server configuration
export PORT="8080"  # Server port (optional)
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
2. Comment: `/factory build` (or `/factory`) as the first line of the comment
3. The webhook receiver creates a Work Order

Only repository owners, organization members and collaborators can trigger a
Work Order; `/factory` from anyone else is ignored and logged, and so is
`/factory` on a pull request. Applying the `factory:build` label already
requires triage access.

### What Happens

Phase 2a webhook receiver:
1. Receives the webhook event
2. Validates the signature
3. Parses the issue title/body into a Work Order structure
4. Converts the Work Order to a minimal single-task TaskDAG
5. Starts a Temporal workflow (`execute_task_dag`) with the TaskDAG
6. Responds with HTTP 200 and workflow ID

The workflow is started asynchronously in Temporal and runs independently. To monitor:

```bash
# Watch Temporal UI at http://localhost:8233 (local dev)
# Or use the Temporal CLI:
temporal workflow list --namespace default
temporal workflow describe --workflow-id factory-WO-<work-order-id>
```

**Note:** Phase 2a creates a minimal TaskDAG with one Implementer task. Future phases will invoke the Planner agent to decompose Work Orders into multi-task DAGs. The TaskRunner (sandbox spawning) is still stubbed—Stream B/C own that integration.

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

## End-to-End Local Testing

To test the complete issue → workflow path locally:

### 1. Start Temporal Server

```bash
# Start Temporal with Docker
docker run --rm -d -p 7233:7233 -p 8233:8233 \
  --name temporal-dev temporalio/auto-setup:latest

# Temporal UI: http://localhost:8233
```

### 2. Start a Temporal Worker

The worker executes the workflows. You'll need a simple TaskRunner:

```python
# test_worker.py
import asyncio
from temporalio.client import Client
from factory.temporal_supervisor import create_temporal_worker
from factory.schemas import Task, TaskResult, TaskStatus


def dummy_runner(task: Task) -> TaskResult:
    """Dummy runner that returns success."""
    print(f"Running task {task.id}: {task.title}")
    return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED, summary="Done")


async def main():
    client = await Client.connect("localhost:7233", namespace="default")
    worker = await create_temporal_worker(client, "factory-tasks", dummy_runner)
    print("Worker started on task queue: factory-tasks")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
```

Run it:

```bash
python test_worker.py
```

**Note:** This worker uses a dummy TaskRunner. Phase 2b/2c will integrate the real sandbox spawner.

### 3. Start the Webhook Receiver

```bash
export GITHUB_WEBHOOK_SECRET="dev-secret"
export TEMPORAL_HOST="localhost:7233"
export TEMPORAL_NAMESPACE="default"
export TEMPORAL_TASK_QUEUE="factory-tasks"

python -m github_app.server
```

### 4. Expose with ngrok (for real GitHub webhooks)

```bash
ngrok http 8080

# Update GitHub App webhook URL to: https://abc123.ngrok.io/webhooks/github
```

### 5. Trigger from GitHub

1. Create an issue in your pilot repository
2. Add the label `factory:build`, or comment `/factory build`
3. Watch the webhook receiver logs
4. Check Temporal UI at http://localhost:8233 for the workflow

### 6. Local Testing Without GitHub

You can POST a webhook directly:

```bash
# Create test payload
cat > payload.json << 'EOF'
{
  "action": "labeled",
  "issue": {
    "number": 1,
    "title": "Test issue",
    "body": "Implement user authentication",
    "user": {"login": "testuser"}
  },
  "label": {"name": "factory:build"},
  "repository": {
    "full_name": "test/repo",
    "clone_url": "https://github.com/test/repo.git",
    "default_branch": "main"
  }
}
EOF

# Compute signature
PAYLOAD=$(cat payload.json)
SECRET="dev-secret"
SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.*= //')

# Send webhook
curl -X POST http://localhost:8080/webhooks/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: issues" \
  -H "X-Hub-Signature-256: sha256=$SIGNATURE" \
  -d @payload.json
```

Expected response:

```json
{
  "status": "submitted",
  "work_order_id": "WO-test-repo-1",
  "workflow_id": "factory-WO-test-repo-1",
  "workflow_run_id": "..."
}
```

Check Temporal UI to see the workflow running.
