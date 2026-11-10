"""The API key must not be readable from any process environment an agent command can see."""

import os
import subprocess
import sys

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
