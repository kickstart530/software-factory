"""Verification gates: deterministic checks the harness runs itself (AD-8).

An agent's claim that tests pass is never trusted; the harness runs the gates
after ``submit_result`` and only then marks the task succeeded. Gate commands
are defined per repository in ``.factory/gates.yaml``::

    gates:
      unit_tests: {command: "pytest -q", timeout_s: 900}
      lint: {command: "ruff check ."}

A gate an agent requires but the repository does not define fails closed.
Gate commands run code the agent may have written, so they get the same minimal
environment as ``run_command`` and their output is redacted before the agent sees it.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from factory.harness.hooks import find_secrets, redact_secrets
from factory.harness.tools import sandbox_env
from factory.schemas import GateResult

TAIL_CHARS = 4000
GATES_FILE = ".factory/gates.yaml"


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
    """P0 fix: scan full branch diff vs base (main), not only dirty files."""
    start = time.monotonic()
    hits = []

    # First, get the merge-base with main
    base_proc = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/main"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )

    if base_proc.returncode == 0:
        base_ref = base_proc.stdout.strip()
        # Get diff of committed changes
        diff_proc = subprocess.run(
            ["git", "diff", "--name-only", base_ref, "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )

        if diff_proc.returncode == 0:
            # Scan committed changes
            for line in diff_proc.stdout.splitlines():
                path = workspace / line
                if path.is_file():
                    try:
                        found = find_secrets(path.read_text())
                        if found:
                            hits.append(f"{line}: {', '.join(found)}")
                    except (UnicodeDecodeError, OSError):
                        continue

    # Also scan uncommitted changes (git status)
    for p in _changed_files(workspace):
        try:
            found = find_secrets(p.read_text())
            if found:
                rel = str(p.relative_to(workspace))
                hits.append(f"{rel} (uncommitted): {', '.join(found)}")
        except (UnicodeDecodeError, OSError):
            continue

    return GateResult(
        name="secret_scan",
        passed=not hits,
        output_tail="\n".join(hits) or "no secrets found in branch diff or uncommitted changes",
        duration_s=time.monotonic() - start,
    )


BUILTIN_GATES: dict[str, BuiltinGate] = {"secret_scan": secret_scan}


class GateRunner:
    def __init__(self, commands: dict[str, GateCommand] | None = None):
        self.commands = commands or {}

    @classmethod
    def from_workspace(cls, workspace: Path) -> GateRunner:
        """Gates as committed at HEAD; the working-tree file only outside a git repo.

        Reading the committed version means uncommitted edits by an agent can't
        change the gates. The harness also snapshots this config into the run's
        checkpoint at task start, so later commits on the task branch can't either.
        """
        proc = subprocess.run(
            ["git", "show", f"HEAD:{GATES_FILE}"], cwd=workspace, capture_output=True, text=True
        )
        if proc.returncode == 0:
            return cls.from_config(yaml.safe_load(proc.stdout) or {})
        inside_repo = (
            subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"], cwd=workspace, capture_output=True
            ).returncode
            == 0
        )
        cfg = workspace / GATES_FILE
        if inside_repo or not cfg.is_file():
            return cls()  # a repo with no committed gate config: every required gate fails closed
        return cls.from_config(yaml.safe_load(cfg.read_text()) or {})

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> GateRunner:
        return cls({k: GateCommand.model_validate(v) for k, v in raw.get("gates", {}).items()})

    def to_config(self) -> dict[str, Any]:
        return {"gates": {k: v.model_dump() for k, v in self.commands.items()}}

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
                env=sandbox_env(),
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
            output_tail=redact_secrets(output)[-TAIL_CHARS:],
            duration_s=time.monotonic() - start,
        )
