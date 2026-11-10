"""Secrets never reach disk, only go to their own host, and are redacted from output."""

import json
import os
import stat
from unittest.mock import patch

import pytest

from factory.harness.hooks import redact_secrets
from factory.secrets import MintRequest
from factory.secrets.backends.env_file import EnvFileBackend
from factory.secrets.backends.vault import VaultBackend

TOKEN = "ghp_" + "A" * 36


def _req(url="https://github.com/acme/app", tool="git"):
    return MintRequest(task_id="t1", work_order_id="wo1", repo_url=url, tool_name=tool)


@pytest.fixture
def backend(tmp_path):
    with patch.dict(os.environ, {"GITHUB_TOKEN": TOKEN}, clear=False):
        os.environ.pop("GITLAB_TOKEN", None)
        os.environ.pop("BITBUCKET_TOKEN", None)
        yield EnvFileBackend(state_dir=tmp_path)


def test_secret_value_never_written_to_disk(backend, tmp_path):
    backend.mint(_req())
    raw = (tmp_path / "credentials.json").read_text()
    assert TOKEN not in raw and "_secret_value" not in raw
    mode = stat.S_IMODE((tmp_path / "credentials.json").stat().st_mode)
    assert mode == 0o600


def test_legacy_state_with_secrets_is_scrubbed_on_load(tmp_path):
    cred = {"cred_x": {"credential_id": "cred_x", "_secret_value": TOKEN}}
    (tmp_path / "credentials.json").write_text(json.dumps(cred))
    EnvFileBackend(state_dir=tmp_path)
    assert TOKEN not in (tmp_path / "credentials.json").read_text()


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/github.com/acme/app",  # host spoofing via path
        "https://github.com.evil.example/acme/app",  # host spoofing via subdomain
        "https://gitlab.com/acme/app",  # a different provider with no token configured
    ],
)
def test_token_only_minted_for_its_own_host(backend, url):
    with pytest.raises(ValueError, match="No secret configured"):
        backend.mint(_req(url=url))


def test_minted_secret_is_redacted_in_any_output(backend):
    backend.mint(_req())
    assert TOKEN not in redact_secrets(f"x={TOKEN}; done")


def test_custom_minted_secret_without_known_pattern_is_redacted(tmp_path):
    custom = "Zq9-unpatterned-secret-value"
    secrets_file = tmp_path / "s.json"
    secrets_file.write_text(json.dumps({"tools": {"deploy": custom}}))
    EnvFileBackend(state_dir=tmp_path / "st", secrets_file=secrets_file).mint(_req(tool="deploy"))
    assert custom not in redact_secrets(f"echo {custom}")


def test_vault_token_not_reusable_after_restart(tmp_path):
    resp = {"lease_id": "l1", "lease_duration": 600, "data": {"token": "hvs." + "B" * 30}}
    with patch.object(VaultBackend, "_vault_write", return_value=resp):
        v1 = VaultBackend(state_dir=tmp_path)
        cred = v1.mint(_req())
    assert "hvs." not in (tmp_path / "vault_credentials.json").read_text()
    assert "GIT_TOKEN" in v1.inject_credential(cred.credential_id, {})
    v2 = VaultBackend(state_dir=tmp_path)
    with pytest.raises(ValueError, match="mint a new credential"):
        v2.inject_credential(cred.credential_id, {})
