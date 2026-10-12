"""Per-turn checkpoints so any crash, pre-emption or human wait can resume."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from factory.schemas import Usage


class RunState(BaseModel):
    """Everything needed to continue a run exactly where it stopped."""

    task_id: str
    agent: str
    agent_version: int
    messages: list[dict[str, Any]] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    gate_attempts: int = 0
    nudges: int = 0
    taint_turns_left: int = 0
    # Set while waiting on a human: the escalate call's id and the other results
    # of that turn, which must be sent in the same user message as the answer.
    pending_escalation_id: str | None = None
    pending_results: list[dict[str, Any]] = Field(default_factory=list)
    # Set once the run reaches a terminal status, so a re-run returns it unchanged.
    final_result: dict[str, Any] | None = None


class CheckpointStore(Protocol):
    def save(self, state: RunState) -> str: ...
    def load(self, task_id: str) -> RunState | None: ...


class FileCheckpointStore:
    """Local implementation; the cloud deployment swaps in an object-store backend."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, task_id: str) -> Path:
        d = self.root / task_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, state: RunState) -> str:
        d = self._dir(state.task_id)
        checkpoint_id = f"{time.time_ns()}"
        tmp = d / f"{checkpoint_id}.json.tmp"
        tmp.write_text(state.model_dump_json())
        os.replace(tmp, d / f"{checkpoint_id}.json")
        (d / "LATEST").write_text(checkpoint_id)
        return checkpoint_id

    def load(self, task_id: str) -> RunState | None:
        latest = self.root / task_id / "LATEST"
        if not latest.is_file():
            return None
        path = self.root / task_id / f"{latest.read_text().strip()}.json"
        return RunState.model_validate(json.loads(path.read_text()))
