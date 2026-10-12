"""Verification gates: deterministic checks the harness runs itself (AD-8).

An agent's claim that tests pass is never trusted; the harness runs the gates
after ``submit_result`` and only then marks the task succeeded. Gate commands
are defined per repository in ``.factory/gates.yaml``::

    gates:
      unit_tests: {command: "pytest -q", timeout_s: 900}
      lint: {command: "ruff check ."}

A gate an agent requires but the repository does not define fails closed.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from factory.harness.hooks import find_secrets
from factory.schemas import GateResult

TAIL_CHARS = 4000


class GateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str
    timeout_s: int = Field(default=900, ge=1)


BuiltinGate = Callable[[Path], GateResult]


def _changed_files(workspace: Path) -> list[Path]:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return [p for p in workspace.rglob("*") if p.is_file() and ".git" not in p.parts]
    files = []
    for line in proc.stdout.splitlines():
        path = line[3:].split(" -> ")[-1].strip('"')
        if (workspace / path).is_file():
            files.append(workspace / path)
    return files


def secret_scan(workspace: Path) -> GateResult:
    start = time.monotonic()
    hits = []
    for p in _changed_files(workspace):
        try:
            found = find_secrets(p.read_text())
        except (UnicodeDecodeError, OSError):
            continue
        if found:
            hits.append(f"{p.relative_to(workspace)}: {', '.join(found)}")
    return GateResult(
        name="secret_scan",
        passed=not hits,
        output_tail="\n".join(hits) or "no secrets found in changed files",
        duration_s=time.monotonic() - start,
    )


BUILTIN_GATES: dict[str, BuiltinGate] = {"secret_scan": secret_scan}


class GateRunner:
    def __init__(self, commands: dict[str, GateCommand] | None = None):
        self.commands = commands or {}

    @classmethod
    def from_workspace(cls, workspace: Path) -> GateRunner:
        cfg = workspace / ".factory" / "gates.yaml"
        if not cfg.is_file():
            return cls()
        raw = yaml.safe_load(cfg.read_text()) or {}
        return cls({k: GateCommand.model_validate(v) for k, v in raw.get("gates", {}).items()})

    def run(self, names: list[str], workspace: Path) -> list[GateResult]:
        return [self.run_one(n, workspace) for n in names]

    def run_one(self, name: str, workspace: Path) -> GateResult:
        if name in self.commands:
            return self._run_command(name, self.commands[name], workspace)
        if name in BUILTIN_GATES:
            return BUILTIN_GATES[name](workspace)
        return GateResult(
            name=name,
            passed=False,
            output_tail=f"gate {name!r} is required but not defined in .factory/gates.yaml",
        )

    @staticmethod
    def _run_command(name: str, gate: GateCommand, workspace: Path) -> GateResult:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                ["bash", "-lc", gate.command],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=gate.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return GateResult(
                name=name,
                passed=False,
                output_tail=f"timed out after {gate.timeout_s}s",
                duration_s=time.monotonic() - start,
            )
        output = proc.stdout + proc.stderr
        return GateResult(
            name=name,
            passed=proc.returncode == 0,
            exit_code=proc.returncode,
            output_tail=output[-TAIL_CHARS:],
            duration_s=time.monotonic() - start,
        )
