"""The API key must not be readable from any process environment an agent command can see."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="uses /proc")


CHILD = r"""
import subprocess, sys
sys.path.insert(0, ROOT)
from factory.harness.credentials import load_api_key
key = load_api_key()
own = open("/proc/self/environ", "rb").read()
leak = subprocess.run(["bash", "-c", "cat /proc/$PPID/environ; ls /proc/$PPID/fd"],
                      capture_output=True).stdout
print("KEY_OK" if key == "sk-ant-" + "q" * 30 else "KEY_BAD")
print("SELF_LEAK" if b"sk-ant-" in own else "SELF_CLEAN")
print("CHILD_LEAK" if b"sk-ant-" in leak else "CHILD_CLEAN")
"""


def test_reexec_hands_key_over_without_environment(tmp_path):
    from tests.conftest import REPO_ROOT

    child = CHILD.replace("ROOT", repr(str(REPO_ROOT)))
    parent = (
        f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from factory.harness.credentials import reexec_without_key\n"
        f"reexec_without_key([sys.executable, '-c', {child!r}])\n"
        "print('NOT_REEXECED')\n"
    )
    env = {**os.environ, "ANTHROPIC_API_KEY": "sk-ant-" + "q" * 30}
    env.pop("FACTORY_API_KEY_FD", None)
    out = subprocess.run(
        [sys.executable, "-c", parent], env=env, capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    lines = out.stdout.split()
    assert lines == ["KEY_OK", "SELF_CLEAN", "CHILD_CLEAN"], out.stdout + out.stderr


def test_load_api_key_falls_back_to_environment(monkeypatch):
    from factory.harness import credentials

    credentials._reset_for_tests()
    monkeypatch.delenv("FACTORY_API_KEY_FD", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", " sk-ant-abc ")
    assert credentials.load_api_key() == "sk-ant-abc"
    credentials._reset_for_tests()


def test_no_reexec_without_key(monkeypatch):
    from factory.harness import credentials

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    credentials.reexec_without_key(["/bin/false"])  # returns instead of exec'ing


MEM_PROBE = r"""
import os, sys
pid = int(sys.argv[1])
found = False
try:
    for line in open(f"/proc/{pid}/maps"):
        parts = line.split()
        if "r" not in parts[1]:
            continue
        a, b = (int(x, 16) for x in parts[0].split("-"))
        try:
            with open(f"/proc/{pid}/mem", "rb") as m:
                m.seek(a)
                if b"sk-ant-" + b"m" * 30 in m.read(b - a):
                    found = True
        except Exception:
            pass
    print("MEM_LEAK" if found else "MEM_CLEAN")
except PermissionError:
    print("MEM_DENIED")
"""

HOLDER = r"""
import os, subprocess, sys
sys.path.insert(0, ROOT)
from factory.harness import credentials
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-" + "m" * 30
if HARDEN:
    key = credentials.load_api_key()
else:
    key = os.environ["ANTHROPIC_API_KEY"]
print(subprocess.run([sys.executable, "-c", PROBE, str(os.getpid())],
                     capture_output=True, text=True).stdout.strip())
"""


def _holder(harden: bool) -> str:
    """Run a process that holds the key, as a non-root user, and probe its memory."""
    from tests.conftest import REPO_ROOT

    code = (
        HOLDER.replace("ROOT", repr(str(REPO_ROOT)))
        .replace("HARDEN", str(harden))
        .replace("PROBE", repr(MEM_PROBE))
    )
    # Root can read any process's memory, so the check only means something
    # between two processes of the same unprivileged user.
    as_user = {"user": 65534, "group": 65534} if os.geteuid() == 0 else {}
    env = {"PATH": os.environ["PATH"], "HOME": "/tmp"}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env, **as_user
    )
    if out.returncode != 0:
        pytest.skip(f"cannot run as an unprivileged user here: {out.stderr.strip()[-200:]}")
    return out.stdout.strip()


def test_hardened_process_memory_is_unreadable_to_same_user_children():
    assert _holder(harden=True) == "MEM_DENIED"


def test_memory_probe_detects_an_unhardened_process():
    """The probe is real: without hardening it finds the key (unless Yama blocks it)."""
    try:
        yama = int(Path("/proc/sys/kernel/yama/ptrace_scope").read_text())
    except OSError:
        yama = 0
    if yama:
        pytest.skip("Yama ptrace_scope already blocks same-user memory reads")
    assert _holder(harden=False) == "MEM_LEAK"
