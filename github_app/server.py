"""Development server for GitHub App webhook receiver.

Run locally for testing with ngrok:
    python -m github_app.server

Configure with environment variables:
    GITHUB_WEBHOOK_SECRET: Webhook secret from GitHub App settings
    TEMPORAL_HOST: Temporal server address (optional, defaults to localhost:7233)
    TEMPORAL_NAMESPACE: Temporal namespace (optional, defaults to default)
    TEMPORAL_TASK_QUEUE: Task queue name (optional, defaults to factory-tasks)
    PORT: Server port (optional, defaults to 8080)
"""

import asyncio
import logging
import os
import sys

from temporalio.client import Client

from github_app.receiver import create_webhook_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


async def create_temporal_client() -> Client | None:
    """Create Temporal client from environment configuration."""
    temporal_host = os.getenv("TEMPORAL_HOST", "localhost:7233")
    temporal_namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    
    try:
        logger.info(f"Connecting to Temporal at {temporal_host}, namespace {temporal_namespace}")
        client = await Client.connect(temporal_host, namespace=temporal_namespace)
        
        # Store task queue on client for webhook receiver to use
        client._task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "factory-tasks")
        
        logger.info("Temporal client connected successfully")
        return client
    except Exception as e:
        logger.error(f"Failed to connect to Temporal: {e}")
        logger.warning("Webhook receiver will run in stub mode (logs only, no workflow starts)")
        return None


def main():
    """Start the webhook receiver server."""
    webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if not webhook_secret:
        logger.warning(
            "GITHUB_WEBHOOK_SECRET not set, using default (INSECURE for development only)"
        )

    port = int(os.getenv("PORT", "8080"))

    logger.info(f"Starting GitHub App webhook receiver on port {port}")
    logger.info("Webhook endpoint: POST /webhooks/github")
    logger.info("Health check: GET /health")

    # Create Temporal client
    temporal_client = asyncio.run(create_temporal_client())

    # Create client factory that returns the connected client
    def temporal_client_factory():
        return temporal_client

    app = create_webhook_app(
        temporal_client_factory=temporal_client_factory,
        webhook_secret=webhook_secret,
    )

    # Run with debug mode in development
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)


if __name__ == "__main__":
    main()
