"""Structured run events. One event per model call, tool call, policy decision and gate.

``JsonlTracer`` is the local sink; the cloud deployment adds an OpenTelemetry
exporter implementing the same ``Tracer`` interface.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol


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
