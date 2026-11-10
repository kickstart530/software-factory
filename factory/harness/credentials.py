"""Keep the model API key out of every environment an agent command can read.

Agent shell commands and gates run with a minimal environment, but a child
process can still read its parent's *original* environment block through
``/proc/<ppid>/environ``. Deleting the key from ``os.environ`` does not change
that block, so the only fix is a process that never had the key in its
environment.

``reexec_without_key`` does that: if ``ANTHROPIC_API_KEY`` is set, it writes the
key into a pipe, then ``execve``s the same command with the key removed from
the environment and the pipe's read end passed as ``FACTORY_API_KEY_FD``. The
new process reads the key once, closes the descriptor, and keeps it only in
memory. Reading another process's memory needs ptrace access, which the
default Yama policy (``ptrace_scope=1``) denies to children.
"""

from __future__ import annotations

import os
import sys

API_KEY_ENV = "ANTHROPIC_API_KEY"
API_KEY_FD_ENV = "FACTORY_API_KEY_FD"

_api_key: str | None = None


def reexec_without_key(exec_args: list[str] | None = None) -> None:
    """Re-exec this process without the API key in its environment. Returns only if no-op."""
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key or os.environ.get(API_KEY_FD_ENV) or not sys.platform.startswith("linux"):
        return
    read_fd, write_fd = os.pipe()
    os.write(write_fd, key.encode())  # far below the pipe buffer, so this never blocks
    os.close(write_fd)
    os.set_inheritable(read_fd, True)
    env = {k: v for k, v in os.environ.items() if k != API_KEY_ENV}
    env[API_KEY_FD_ENV] = str(read_fd)
    args = exec_args or [sys.executable, "-m", "factory.cli", *sys.argv[1:]]
    os.execve(args[0], args, env)


def load_api_key() -> str:
    """The API key, from the hand-over pipe if present, else the environment.

    The pipe can be read only once, so its key is kept for the life of the process.
    """
    global _api_key
    if _api_key is not None:
        return _api_key
    fd = os.environ.pop(API_KEY_FD_ENV, None)
    if fd is None:
        return os.environ.get(API_KEY_ENV, "").strip()
    chunks = []
    with os.fdopen(int(fd), "rb", closefd=True) as pipe:
        while chunk := pipe.read(4096):
            chunks.append(chunk)
    _api_key = b"".join(chunks).decode().strip()
    return _api_key


def _reset_for_tests() -> None:
    global _api_key
    _api_key = None
