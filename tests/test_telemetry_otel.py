"""Tests for OpenTelemetry tracer."""

import pytest

from factory.harness.telemetry import JsonlTracer, OtelTracer


def test_jsonl_tracer_fallback(tmp_path):
    """JsonlTracer works as fallback when OTel is not available."""
    path = tmp_path / "traces.jsonl"
    tracer = JsonlTracer(path, task_id="T1")

    tracer.event("task.started", agent="test")
    tracer.event("model.call", model="test-model", input_tokens=100)
    tracer.event("tool.call", tool="read_file", is_error=False)
    tracer.event("task.finished", status="succeeded")

    assert path.exists()
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 4

    import json

    for line in lines:
        record = json.loads(line)
        assert "ts" in record
        assert "event" in record
        assert "task_id" in record
        assert record["task_id"] == "T1"


def test_otel_tracer_basic_events(tmp_path, monkeypatch):
    """OtelTracer exports basic events without crashing."""
    pytest.importorskip("opentelemetry.sdk")

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    fallback = tmp_path / "fallback.jsonl"
    tracer = OtelTracer(service_name="test-factory", fallback_jsonl=fallback)

    tracer.event("task.started", task_id="T1", agent="test")
    tracer.event("model.call", task_id="T1", model="test-model", input_tokens=100)
    tracer.event("tool.call", task_id="T1", tool="read_file", is_error=False)
    tracer.event("task.finished", task_id="T1", status="succeeded")

    assert fallback.exists()
    lines = fallback.read_text().strip().split("\n")
    assert len(lines) == 4


def test_otel_tracer_handles_events_without_task_id(tmp_path, monkeypatch):
    """OtelTracer handles standalone events without task_id."""
    pytest.importorskip("opentelemetry.sdk")

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    fallback = tmp_path / "fallback.jsonl"
    tracer = OtelTracer(fallback_jsonl=fallback)

    tracer.event("system.startup", version="1.0")
    tracer.event("system.shutdown")

    assert fallback.exists()
    lines = fallback.read_text().strip().split("\n")
    assert len(lines) == 2


def test_otel_tracer_respects_endpoint_env_var(tmp_path, monkeypatch):
    """OtelTracer uses OTLP exporter when endpoint is set."""
    pytest.importorskip("opentelemetry.sdk")

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    fallback = tmp_path / "fallback.jsonl"
    tracer = OtelTracer(fallback_jsonl=fallback)

    tracer.event("test.event", task_id="T1")

    assert fallback.exists()
