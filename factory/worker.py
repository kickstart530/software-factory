"""Temporal worker: `python -m factory.worker`.

Polls the factory task queue, runs ExecuteTaskDAGWorkflow, and executes each task
in an ephemeral sandbox pod (factory.sandbox.run_task_in_sandbox). Serves the
liveness and readiness endpoints the worker Deployment probes.

Environment (names match infra/workers/base/deployment.yaml):
    TEMPORAL_ADDRESS     Temporal frontend, host:port (default localhost:7233)
    TEMPORAL_NAMESPACE   Temporal namespace (default "default")
    TASK_QUEUE           Task queue to poll (default "factory-tasks")
    FACTORY_STATE_DIR    Directory holding the kill switch (default /var/lib/factory)
    HEALTH_PORT          Port for /health/live and /health/ready (default 8080)

The worker never calls the model itself, so it needs no API key; sandbox pods
read theirs from a Kubernetes Secret (see SandboxConfig.api_key_secret).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from factory.control import KillSwitch
from factory.control.kill_switch import FileKillSwitchStore
from factory.sandbox import run_task_in_sandbox
from factory.temporal_supervisor import TemporalConfig, create_temporal_worker

logger = logging.getLogger("factory.worker")


class _Health:
    ready = threading.Event()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path == "/health/live":
            ok = True
        elif self.path == "/health/ready":
            ok = _Health.ready.is_set()
        else:
            self.send_error(404)
            return
        self.send_response(200 if ok else 503)
        self.end_headers()
        self.wfile.write(b"ok" if ok else b"not ready")

    def log_message(self, *args) -> None:  # keep probe traffic out of the logs
        pass


def serve_health(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True, name="health").start()
    return server


async def run_worker() -> None:
    config = TemporalConfig(
        host=os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
        task_queue=os.getenv("TASK_QUEUE", "factory-tasks"),
    )
    state_dir = Path(os.getenv("FACTORY_STATE_DIR", "/var/lib/factory"))
    kill_switch = KillSwitch(FileKillSwitchStore(state_dir / "kill_switch"))

    logger.info(f"Connecting to Temporal at {config.host} (namespace {config.namespace})")
    client = await config.create_client()
    worker = await create_temporal_worker(
        client, config.task_queue, run_task_in_sandbox, kill_switch=kill_switch
    )
    _Health.ready.set()
    logger.info(f"Polling task queue {config.task_queue}")
    try:
        await worker.run()
    finally:
        _Health.ready.clear()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    serve_health(int(os.getenv("HEALTH_PORT", "8080")))
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
