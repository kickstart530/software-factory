"""Tool registry and the built-in sandbox tools.

Every tool takes a Pydantic input model, so arguments are validated before the
handler runs (the model's tool input is untrusted output). File tools are
confined to the task workspace; large outputs are spilled to the artifacts
directory and replaced by a head/tail excerpt with a pointer.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_OUTPUT_CHARS = 20_000
SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "VIRTUAL_ENV")


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class ToolContext:
    workspace: Path
    artifacts_dir: Path
    env: dict[str, str] = field(default_factory=dict)
    command_timeout_s: int = 600

    def __post_init__(self) -> None:
        self.workspace = self.workspace.resolve()
        self.artifacts_dir = self.artifacts_dir.resolve()
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def resolve(self, path: str, *, for_write: bool = False) -> Path:
        """Resolve ``path`` inside the workspace (or, for reads, the artifacts dir)."""
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        resolved = candidate.resolve()
        roots = [self.workspace] if for_write else [self.workspace, self.artifacts_dir]
        if not any(resolved == r or resolved.is_relative_to(r) for r in roots):
            raise ToolError(f"path {path!r} is outside the workspace")
        if resolved.is_relative_to(self.workspace / ".git"):
            raise ToolError("direct access to .git is not allowed; use git via run_command")
        return resolved

    def spill(self, text: str, label: str) -> str:
        """Keep large outputs out of context: save in full, return an excerpt + pointer."""
        if len(text) <= MAX_OUTPUT_CHARS:
            return text
        path = self.artifacts_dir / f"{label}-{uuid.uuid4().hex[:8]}.txt"
        path.write_text(text)
        half = MAX_OUTPUT_CHARS // 2
        return (
            f"{text[:half]}\n\n... [{len(text) - MAX_OUTPUT_CHARS} chars omitted; full output at "
            f"{path} - use read_file with offset/limit] ...\n\n{text[-half:]}"
        )


class ToolError(Exception):
    """A tool failure reported back to the model as an is_error result."""


@dataclass
class ToolOutput:
    content: str
    is_error: bool = False
    control: Literal["submit", "escalate"] | None = None
    payload: dict[str, Any] | None = None


Handler = Callable[[ToolContext, Any], ToolOutput | str]


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Handler
    mutates: bool = False
    trust: Literal["trusted", "untrusted"] = "trusted"
    sensitive: bool = False  # denied while the context holds fresh untrusted content

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }

    def invoke(self, ctx: ToolContext, raw_input: Any) -> ToolOutput:
        try:
            args = self.input_model.model_validate(raw_input)
        except ValidationError as e:
            return ToolOutput(f"Invalid input for {self.name}: {e}", is_error=True)
        try:
            out = self.handler(ctx, args)
        except ToolError as e:
            return ToolOutput(str(e), is_error=True)
        return out if isinstance(out, ToolOutput) else ToolOutput(out)


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool.name):
            raise ValueError(f"invalid tool name {tool.name!r}")
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def subset(self, names: list[str]) -> ToolRegistry:
        missing = [n for n in names if n not in self._tools]
        if missing:
            raise KeyError(f"unknown tools: {missing}")
        return ToolRegistry([self._tools[n] for n in names])

    def definitions(self) -> list[dict[str, Any]]:
        # Sorted for a byte-stable prompt prefix (prompt caching).
        return [self._tools[n].definition() for n in sorted(self._tools)]

    def names(self) -> list[str]:
        return sorted(self._tools)


# --- Built-in tools ---------------------------------------------------------


class ReadFileInput(ToolInput):
    path: str
    offset: int = Field(default=0, ge=0, description="First line to return (0-based).")
    limit: int = Field(default=2000, ge=1, le=5000, description="Maximum lines to return.")


def _read_file(ctx: ToolContext, a: ReadFileInput) -> str:
    p = ctx.resolve(a.path)
    if not p.is_file():
        raise ToolError(f"{a.path}: not a file")
    lines = p.read_text(errors="replace").splitlines()
    window = lines[a.offset : a.offset + a.limit]
    body = "\n".join(f"{a.offset + i + 1}\t{line}" for i, line in enumerate(window))
    more = len(lines) - (a.offset + len(window))
    suffix = f"\n[{more} more lines; continue with offset={a.offset + len(window)}]" if more else ""
    return ctx.spill(body + suffix, "read")


class WriteFileInput(ToolInput):
    path: str
    content: str


def _write_file(ctx: ToolContext, a: WriteFileInput) -> str:
    p = ctx.resolve(a.path, for_write=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(a.content)
    return f"wrote {len(a.content)} chars to {p.relative_to(ctx.workspace)}"


class EditFileInput(ToolInput):
    path: str
    old: str = Field(min_length=1, description="Exact text to replace; must occur exactly once.")
    new: str


def _edit_file(ctx: ToolContext, a: EditFileInput) -> str:
    p = ctx.resolve(a.path, for_write=True)
    if not p.is_file():
        raise ToolError(f"{a.path}: not a file")
    text = p.read_text()
    count = text.count(a.old)
    if count != 1:
        raise ToolError(f"'old' must match exactly once in {a.path}; matched {count} times")
    p.write_text(text.replace(a.old, a.new, 1))
    return f"edited {p.relative_to(ctx.workspace)}"


class ListFilesInput(ToolInput):
    pattern: str = Field(default="**/*", description="Glob relative to the workspace root.")
    limit: int = Field(default=500, ge=1, le=5000)


def _list_files(ctx: ToolContext, a: ListFilesInput) -> str:
    out: list[str] = []
    for p in sorted(ctx.workspace.glob(a.pattern)):
        rel = p.relative_to(ctx.workspace)
        if rel.parts and rel.parts[0] == ".git" or not p.is_file():
            continue
        out.append(str(rel))
        if len(out) >= a.limit:
            out.append(f"[truncated at {a.limit} entries]")
            break
    return "\n".join(out) or "(no matches)"


class SearchCodeInput(ToolInput):
    regex: str
    glob: str = "**/*"
    max_results: int = Field(default=200, ge=1, le=2000)


def _search_code(ctx: ToolContext, a: SearchCodeInput) -> str:
    try:
        rx = re.compile(a.regex)
    except re.error as e:
        raise ToolError(f"invalid regex: {e}") from e
    hits: list[str] = []
    for p in sorted(ctx.workspace.glob(a.glob)):
        rel = p.relative_to(ctx.workspace)
        if not p.is_file() or (rel.parts and rel.parts[0] == ".git"):
            continue
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:300]}")
                if len(hits) >= a.max_results:
                    return "\n".join(hits) + f"\n[stopped at {a.max_results} results]"
    return "\n".join(hits) or "(no matches)"


class RunCommandInput(ToolInput):
    command: str = Field(min_length=1)
    timeout_s: int | None = Field(default=None, ge=1, le=3600)


def _run_command(ctx: ToolContext, a: RunCommandInput) -> ToolOutput:
    env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ} | ctx.env
    try:
        proc = subprocess.run(
            ["bash", "-lc", a.command],
            cwd=ctx.workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=a.timeout_s or ctx.command_timeout_s,
        )
    except subprocess.TimeoutExpired:
        return ToolOutput(f"command timed out after {a.timeout_s or ctx.command_timeout_s}s", True)
    text = (
        f"exit_code={proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    return ToolOutput(ctx.spill(text, "cmd"), is_error=proc.returncode != 0)


class EscalateInput(ToolInput):
    reason: str = Field(min_length=1)
    question: str = Field(min_length=1, description="One question a human can answer quickly.")
    options: list[str] = Field(default_factory=list, max_length=5)


def _escalate(ctx: ToolContext, a: EscalateInput) -> ToolOutput:
    return ToolOutput("escalated to a human", control="escalate", payload=a.model_dump())


def builtin_tools() -> list[Tool]:
    return [
        Tool("read_file", "Read a text file with line numbers.", ReadFileInput, _read_file),
        Tool(
            "write_file",
            "Create or overwrite a file in the workspace.",
            WriteFileInput,
            _write_file,
            mutates=True,
        ),
        Tool(
            "edit_file",
            "Replace one exact, unique occurrence of text in a file.",
            EditFileInput,
            _edit_file,
            mutates=True,
        ),
        Tool("list_files", "List workspace files matching a glob.", ListFilesInput, _list_files),
        Tool("search_code", "Regex search over workspace files.", SearchCodeInput, _search_code),
        Tool(
            "run_command",
            "Run a shell command in the workspace (build, test, git). Network is restricted.",
            RunCommandInput,
            _run_command,
            mutates=True,
            sensitive=True,
        ),
        Tool(
            "escalate",
            "Pause and ask a human when blocked, the request is ambiguous, or the change is "
            "riskier than the task allows. Prefer this over guessing.",
            EscalateInput,
            _escalate,
        ),
    ]


def submit_result_tool(output_model: type[BaseModel]) -> Tool:
    """The terminal tool. Its input schema is the agent's output contract."""

    def _submit(ctx: ToolContext, a: BaseModel) -> ToolOutput:
        return ToolOutput("result received", control="submit", payload=a.model_dump(mode="json"))

    return Tool(
        "submit_result",
        "Finish the task with your final, structured result. The harness then runs the "
        "verification gates; if any fail you will get the output and can keep working.",
        output_model,
        _submit,
    )
