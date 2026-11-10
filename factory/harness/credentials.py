"""Keep the model API key away from the commands an agent runs.

Agent shell commands and gates run with a minimal environment, but they run as
the same user as the harness, and Linux gives same-user processes two ways in:

1. ``/proc/<pid>/environ`` shows a process's *original* environment block.
   Deleting the key from ``os.environ`` does not change that block.
   ``reexec_without_key`` fixes this: it writes the key into a pipe, then
   ``execve``s the same command with the key removed from the environment and
   the pipe's read end passed as ``FACTORY_API_KEY_FD``. The new process reads
   the key once and keeps it only in memory.
2. ``/proc/<pid>/mem`` (and ptrace) expose that memory. Yama's ptrace_scope
   blocks this on many distributions, but not every kernel has it.
   ``harden_process`` marks the process non-dumpable, which makes the kernel
   deny same-user access to its memory and its ``/proc`` entries.

Neither helps against root or a different sandbox escape; the sandbox pod's
runtime isolation (gVisor) and a model gateway that holds the key are the
layers above this.
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


PR_SET_DUMPABLE = 4


def harden_process() -> bool:
    """Make this process's memory and /proc entries unreadable to same-user processes.

    Must run after any exec (the flag resets on execve) and before the first agent
    command. Returns False where it isn't supported (non-Linux).
    """
    if not sys.platform.startswith("linux"):
        return False
    import ctypes

    try:
        return ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def load_api_key() -> str:
    """The API key, from the hand-over pipe if present, else the environment.

    The pipe can be read only once, so its key is kept for the life of the process.
    """
    global _api_key
    if _api_key is not None:
        return _api_key
    harden_process()  # before the key enters this process's memory
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
