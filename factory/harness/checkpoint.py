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
    work_order_id: str | None = None  # P0 fix: needed for proper checkpoint keying
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
    # Gate config captured when the task started; a resume uses this snapshot, never
    # the workspace, so an agent can't relax its own gates mid-task.
    gates_config: dict[str, Any] | None = None
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

    def _dir(self, task_id: str, work_order_id: str | None = None) -> Path:
        # P0 fix: key checkpoints by work_order_id + task_id to avoid collisions
        d = (
            self.root / work_order_id / task_id
            if work_order_id
            else self.root / task_id  # Backward compat
        )
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, state: RunState) -> str:
        d = self._dir(state.task_id, state.work_order_id)
        checkpoint_id = f"{time.time_ns()}"
        tmp = d / f"{checkpoint_id}.json.tmp"
        tmp.write_text(state.model_dump_json())
        os.replace(tmp, d / f"{checkpoint_id}.json")
        (d / "LATEST").write_text(checkpoint_id)
        return checkpoint_id

    def load(self, task_id: str, work_order_id: str | None = None) -> RunState | None:
        # P0 fix: try work_order_id/task_id first, fall back to task_id for backward compat
        if work_order_id:
            latest = self.root / work_order_id / task_id / "LATEST"
            if latest.is_file():
                path = self.root / work_order_id / task_id / f"{latest.read_text().strip()}.json"
                return RunState.model_validate(json.loads(path.read_text()))

        # Fallback to old path for backward compat
        latest = self.root / task_id / "LATEST"
        if latest.is_file():
            path = self.root / task_id / f"{latest.read_text().strip()}.json"
            return RunState.model_validate(json.loads(path.read_text()))

        # Final fallback: search all work_order subdirs for this task_id
        # This handles tests that load("T1") when checkpoint was saved with work_order_id
        if not work_order_id and self.root.exists():
            for wo_dir in self.root.iterdir():
                if wo_dir.is_dir():
                    latest = wo_dir / task_id / "LATEST"
                    if latest.is_file():
                        path = wo_dir / task_id / f"{latest.read_text().strip()}.json"
                        return RunState.model_validate(json.loads(path.read_text()))

        return None
