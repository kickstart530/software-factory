"""Structured run events. One event per model call, tool call, policy decision and gate.

``JsonlTracer`` is the local sink; ``OtelTracer`` exports spans to OpenTelemetry.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False


class Tracer(Protocol):
    def event(self, name: str, **attrs: Any) -> None: ...


class NullTracer:
    def event(self, name: str, **attrs: Any) -> None:
        pass


class MemoryTracer:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def event(self, name: str, **attrs: Any) -> None:
        self.events.append({"event": name, **attrs})

    def named(self, name: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["event"] == name]


class JsonlTracer:
    def __init__(self, path: Path, **base: Any):
        self.path = path
        self.base = base
        path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, name: str, **attrs: Any) -> None:
        record = {"ts": time.time(), "event": name, **self.base, **attrs}
        with self.path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")


class OtelTracer:
    """OpenTelemetry exporter for the Tracer interface.

    Exports spans for turns, tools, and model calls. Each event becomes a span
    with the event name as the span name and attributes from the event kwargs.

    The tracer automatically creates a root span per task_id. Nested events
    (tool.call, model.call) are attached as child spans if they occur within
    the same task context.

    Falls back to console export if OTEL_EXPORTER_OTLP_ENDPOINT is not set.
    """

    def __init__(self, service_name: str = "software-factory", fallback_jsonl: Path | None = None):
        if not _OTEL_AVAILABLE:
            raise ImportError(
                "OpenTelemetry SDK not installed. Install with: pip install opentelemetry-sdk "
                "opentelemetry-exporter-otlp-proto-grpc"
            )

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        import os

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            exporter = OTLPSpanExporter(endpoint=endpoint)
        else:
            exporter = ConsoleSpanExporter()

        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        self.tracer = trace.get_tracer(__name__)
        self.task_spans: dict[str, Any] = {}
        self.fallback = JsonlTracer(fallback_jsonl) if fallback_jsonl else None

    def event(self, name: str, **attrs: Any) -> None:
        if self.fallback:
            self.fallback.event(name, **attrs)

        task_id = attrs.get("task_id")
        if not task_id:
            with self.tracer.start_as_current_span(name) as span:
                self._set_attributes(span, attrs)
            return

        if name == "task.finished":
            if task_id in self.task_spans:
                span = self.task_spans.pop(task_id)
                self._set_attributes(span, attrs)
                span.end()
            return

        if task_id not in self.task_spans:
            self.task_spans[task_id] = self.tracer.start_span(f"task.{task_id}")

        parent_ctx = trace.set_span_in_context(self.task_spans[task_id])
        with self.tracer.start_as_current_span(name, context=parent_ctx) as span:
            self._set_attributes(span, attrs)

    @staticmethod
    def _set_attributes(span: Any, attrs: dict[str, Any]) -> None:
        for k, v in attrs.items():
            if k == "task_id":
                continue
            if isinstance(v, (str, int, float, bool)):
                span.set_attribute(k, v)
            else:
                span.set_attribute(k, str(v))
