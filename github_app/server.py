"""Development server for GitHub App webhook receiver.

Run locally for testing with ngrok:
    python -m github_app.server

Configure with environment variables:
    GITHUB_WEBHOOK_SECRET: Webhook secret from GitHub App settings
    TEMPORAL_HOST: Temporal server address (optional, defaults to localhost:7233)
    PORT: Server port (optional, defaults to 8080)
"""

import logging
import os
import sys

from github_app.receiver import create_webhook_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


def main():
    """Start the webhook receiver server."""
    webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if not webhook_secret:
        logger.warning(
            "GITHUB_WEBHOOK_SECRET not set, using default (INSECURE for development only)"
        )

    port = int(os.getenv("PORT", "8080"))
    temporal_host = os.getenv("TEMPORAL_HOST", "localhost:7233")

    logger.info(f"Starting GitHub App webhook receiver on port {port}")
    logger.info(f"Temporal host: {temporal_host}")
    logger.info("Webhook endpoint: POST /webhooks/github")
    logger.info("Health check: GET /health")

    # For Phase 2a, Temporal client factory is stubbed
    # Integration with Temporal happens after sandbox/worker components are ready
    def temporal_client_factory():
        # TODO: Create and return Temporal client
        # This will be implemented after Stream B/C complete sandbox/worker injection
        logger.info("Temporal client factory called (stubbed for Phase 2a)")
        return None

    app = create_webhook_app(
        temporal_client_factory=temporal_client_factory,
        webhook_secret=webhook_secret,
    )

    # Run with debug mode in development
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)


if __name__ == "__main__":
    main()
