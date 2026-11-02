#!/usr/bin/env python3
"""
Hello-world sandbox that emits traces and logs to prove the observability pipeline.
"""

import os
import platform
import sys
import time
from datetime import UTC, datetime

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def _timestamp() -> str:
    """Return ISO8601 timestamp for logging."""
    return datetime.now(UTC).isoformat()


def setup_tracing():
    """Configure OpenTelemetry to export to the OTel collector."""
    otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "otel-collector.factory-system:4317")

    resource = Resource.create(
        {
            "service.name": "factory.sandbox.hello-world",
            "service.version": "0.1.0",
            "deployment.environment": os.getenv("FACTORY_ENV", "local"),
            "sandbox.type": "hello-world",
        }
    )

    provider = TracerProvider(resource=resource)

    # Export to OTel collector
    otlp_exporter = OTLPSpanExporter(
        endpoint=otel_endpoint,
        insecure=True,  # Use TLS in production
    )

    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    trace.set_tracer_provider(provider)

    return trace.get_tracer(__name__)


def run_hello_world_task(tracer):
    """Execute a trivial task with tracing."""
    with tracer.start_as_current_span("hello_world_task") as span:
        span.set_attribute("task.id", "hello-world-001")
        span.set_attribute("task.type", "proof-of-concept")

        print(
            f"[{_timestamp()}] INFO: Starting hello-world sandbox task",
            flush=True,
        )

        # Simulate some work
        with tracer.start_as_current_span("gather_system_info"):
            system_info = {
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "hostname": platform.node(),
                "architecture": platform.machine(),
            }
            span.set_attribute("system.platform", system_info["platform"])
            span.set_attribute("system.python", system_info["python_version"])
            print(
                f"[{_timestamp()}] INFO: System info: {system_info}",
                flush=True,
            )

        with tracer.start_as_current_span("simulate_work"):
            span.set_attribute("work.units", 3)
            for i in range(3):
                time.sleep(0.1)
                print(
                    f"[{_timestamp()}] INFO: Work unit {i + 1}/3 complete",
                    flush=True,
                )

        with tracer.start_as_current_span("emit_result"):
            result = {
                "status": "success",
                "message": "Hello from the software factory sandbox!",
                "timestamp": _timestamp(),
            }
            span.set_attribute("result.status", result["status"])
            print(f"[{_timestamp()}] INFO: Task result: {result}", flush=True)

        print(
            f"[{_timestamp()}] INFO: Hello-world task completed successfully",
            flush=True,
        )
        return 0


def main():
    """Main entry point."""
    print(
        f"[{_timestamp()}] INFO: Initializing hello-world sandbox",
        flush=True,
    )

    try:
        tracer = setup_tracing()
        print(f"[{_timestamp()}] INFO: Tracing configured", flush=True)
    except Exception as e:
        print(
            f"[{_timestamp()}] ERROR: Failed to setup tracing: {e}",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"[{_timestamp()}] WARN: Continuing without tracing",
            flush=True,
        )
        tracer = trace.get_tracer(__name__)  # No-op tracer

    try:
        exit_code = run_hello_world_task(tracer)

        # Flush traces before exit
        time.sleep(1)
        print(f"[{_timestamp()}] INFO: Traces flushed", flush=True)

        return exit_code
    except Exception as e:
        print(
            f"[{_timestamp()}] ERROR: Task failed: {e}",
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
