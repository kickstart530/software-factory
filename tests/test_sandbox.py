"""Tests for sandbox pod spawning."""

import pytest

from factory.sandbox.config import (
    EgressPolicy,
    ResourceQuota,
    SandboxConfig,
    SandboxImage,
)
from factory.sandbox.network_policy import create_egress_proxy_config, create_network_policy
from factory.sandbox.spawner import SandboxSpawner


class TestSandboxImage:
    def test_full_name(self):
        image = SandboxImage(repository="example.com/harness", tag="v1.0")
        assert image.full_name == "example.com/harness:v1.0"

    def test_defaults(self):
        image = SandboxImage()
        assert image.repository == "software-factory/agent-harness"
        assert image.tag == "latest"
        assert image.pull_policy == "IfNotPresent"


class TestEgressPolicy:
    def test_deny_all(self):
        policy = EgressPolicy(mode="deny-all")
        assert policy.mode == "deny-all"
        assert policy.allowed_domains == []
        assert policy.allowed_cidrs == []

    def test_allowlist(self):
        policy = EgressPolicy(
            mode="allowlist",
            allowed_domains=["github.com", "pypi.org"],
            allowed_cidrs=["10.0.0.0/8"],
        )
        assert policy.mode == "allowlist"
        assert "github.com" in policy.allowed_domains
        assert "10.0.0.0/8" in policy.allowed_cidrs

    def test_default_factory(self):
        policy = EgressPolicy.default_factory()
        assert policy.mode == "allowlist"
        assert "github.com" in policy.allowed_domains
        assert "pypi.org" in policy.allowed_domains


class TestResourceQuota:
    def test_defaults(self):
        quota = ResourceQuota()
        assert quota.cpu_request == "1000m"
        assert quota.cpu_limit == "2000m"
        assert quota.memory_request == "2Gi"
        assert quota.memory_limit == "4Gi"
        assert quota.ephemeral_storage_limit == "10Gi"


class TestSandboxConfig:
    def test_defaults(self):
        config = SandboxConfig()
        assert config.namespace == "factory-sandboxes"
        assert config.runtime_class == "gvisor"
        assert config.timeout_seconds == 7200
        assert config.service_account == "factory-sandbox-runner"

    def test_labels(self):
        config = SandboxConfig()
        assert config.labels["app"] == "factory-sandbox"
        assert config.labels["managed-by"] == "temporal"

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("FACTORY_SANDBOX_NAMESPACE", "custom-ns")
        monkeypatch.setenv("FACTORY_SANDBOX_RUNTIME_CLASS", "kata")
        monkeypatch.setenv("FACTORY_SANDBOX_IMAGE", "gcr.io/proj/img:v2")
        monkeypatch.setenv("FACTORY_SANDBOX_EGRESS_MODE", "allow-all")

        config = SandboxConfig.from_env()
        assert config.namespace == "custom-ns"
        assert config.runtime_class == "kata"
        assert config.image.repository == "gcr.io/proj/img"
        assert config.image.tag == "v2"
        assert config.egress.mode == "allow-all"

    def test_from_env_no_runtime_class(self, monkeypatch):
        monkeypatch.setenv("FACTORY_SANDBOX_RUNTIME_CLASS", "none")
        config = SandboxConfig.from_env()
        assert config.runtime_class is None


class TestSandboxSpawner:
    def test_pod_name_for_task(self):
        import re

        from factory.schemas import Task

        spawner = SandboxSpawner()

        def name(task_id, wo="WO-acme-app-12"):
            task = Task(id=task_id, work_order_id=wo, agent="implementer", title="T",
                        instructions="x")  # fmt: skip
            return spawner._pod_name_for_task(task)

        dns1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
        for task_id in ["task-001", "Task_002", "_task-003", "a.b", "x" * 120]:
            pod = name(task_id)
            assert dns1123.match(pod) and len(pod) <= 63, pod

        assert name("impl-1").startswith("wo-acme-app-12-impl-1-")
        # Same task id in two Work Orders (every webhook task is impl-1) never collides,
        # and neither do two attempts at the same task.
        assert name("impl-1", "WO-a-1") != name("impl-1", "WO-b-2")
        assert name("impl-1") != name("impl-1")

    def test_create_pod_manifest(self):
        from factory.schemas import Task

        spawner = SandboxSpawner()
        task = Task(
            id="task-001",
            work_order_id="wo-001",
            agent="implementer",
            title="Implement feature X",
            instructions="Write code for feature X",
        )

        manifest = spawner._create_pod_manifest(task, "task-001")

        # Basic structure
        assert manifest["apiVersion"] == "v1"
        assert manifest["kind"] == "Pod"
        assert manifest["metadata"]["name"] == "task-001"
        assert manifest["metadata"]["namespace"] == "factory-sandboxes"

        # Labels
        labels = manifest["metadata"]["labels"]
        assert labels["task-id"] == "task-001"
        assert labels["agent"] == "implementer"
        assert labels["work-order-id"] == "wo-001"

        # Spec
        spec = manifest["spec"]
        assert spec["restartPolicy"] == "Never"
        assert spec["serviceAccountName"] == "factory-sandbox-runner"
        assert spec["runtimeClassName"] == "gvisor"

        # Security context
        security_context = spec["securityContext"]
        assert security_context["fsGroup"] == 1000
        assert security_context["seccompProfile"]["type"] == "RuntimeDefault"

        # Container
        container = spec["containers"][0]
        assert container["name"] == "agent-harness"
        assert container["workingDir"] == "/workspace"

        # Container security context (CRITICAL: no host credentials)
        container_sec = container["securityContext"]
        assert container_sec["runAsNonRoot"] is True
        assert container_sec["runAsUser"] == 1000
        assert container_sec["allowPrivilegeEscalation"] is False
        assert "ALL" in container_sec["capabilities"]["drop"]

        # Environment (CRITICAL: clean HOME, minimal PATH)
        env_map = {e["name"]: e.get("value") for e in container["env"]}
        assert env_map["HOME"] == "/home/sandbox"
        assert env_map["PATH"] == "/usr/local/bin:/usr/bin:/bin"
        assert env_map["GIT_TERMINAL_PROMPT"] == "0"
        assert env_map["TASK_ID"] == "task-001"
        assert Task.model_validate_json(env_map["FACTORY_TASK_JSON"]).id == "task-001"

        # The API key comes from a Secret, never a literal value in the manifest
        key = next(e for e in container["env"] if e["name"] == "ANTHROPIC_API_KEY")
        assert "value" not in key
        assert key["valueFrom"]["secretKeyRef"] == {
            "name": "factory-secrets",
            "key": "anthropic-api-key",
        }
        assert container["command"] == ["factory", "sandbox-entry"]

        # Volumes (CRITICAL: no host mounts)
        volumes = spec["volumes"]
        assert len(volumes) == 2
        volume_names = {v["name"] for v in volumes}
        assert "workspace" in volume_names
        assert "home" in volume_names

        # All volumes are emptyDir (not hostPath)
        for vol in volumes:
            assert "emptyDir" in vol
            assert "hostPath" not in vol

        # Volume mounts
        mounts = container["volumeMounts"]
        mount_map = {m["name"]: m["mountPath"] for m in mounts}
        assert mount_map["workspace"] == "/workspace"
        assert mount_map["home"] == "/home/sandbox"

        # Resources
        resources = container["resources"]
        assert "requests" in resources
        assert "limits" in resources


class TestNetworkPolicy:
    def test_deny_all_policy(self):
        policy = EgressPolicy(mode="deny-all")
        manifest = create_network_policy("factory-sandboxes", policy)

        assert manifest["kind"] == "NetworkPolicy"
        assert manifest["metadata"]["namespace"] == "factory-sandboxes"

        # Deny-all: only DNS egress allowed
        egress = manifest["spec"]["egress"]
        assert len(egress) == 1
        assert egress[0]["ports"][0]["port"] == 53  # DNS

    def test_allow_all_policy(self):
        policy = EgressPolicy(mode="allow-all")
        manifest = create_network_policy("factory-sandboxes", policy)

        egress = manifest["spec"]["egress"]
        assert len(egress) == 1
        assert egress[0] == {}  # Empty rule = allow all

    def test_allowlist_policy_with_cidrs(self):
        policy = EgressPolicy(
            mode="allowlist",
            allowed_cidrs=["10.0.0.0/8", "192.168.1.0/24"],
        )
        manifest = create_network_policy("factory-sandboxes", policy)

        egress = manifest["spec"]["egress"]
        # DNS + 2 CIDRs
        assert len(egress) == 3

        # Find CIDR rules (skip DNS rule)
        cidr_rules = [r for r in egress if "ipBlock" in r.get("to", [{}])[0]]
        assert len(cidr_rules) == 2

    def test_custom_pod_selector(self):
        policy = EgressPolicy(mode="deny-all")
        manifest = create_network_policy(
            "custom-ns",
            policy,
            pod_selector_labels={"custom": "label"},
        )

        assert manifest["metadata"]["namespace"] == "custom-ns"
        assert manifest["spec"]["podSelector"]["matchLabels"]["custom"] == "label"


class TestEgressProxyConfig:
    def test_allowlist_domains(self):
        policy = EgressPolicy(
            mode="allowlist",
            allowed_domains=["github.com", "pypi.org", "api.example.com"],
        )
        config = create_egress_proxy_config(policy)

        assert config["type"] == "squid"
        assert len(config["rules"]) == 3
        assert "acl allowed_domains dstdomain github.com" in config["rules"]

    def test_deny_all_no_config(self):
        policy = EgressPolicy(mode="deny-all")
        config = create_egress_proxy_config(policy)
        assert config == {}

    def test_allowlist_no_domains(self):
        policy = EgressPolicy(mode="allowlist", allowed_cidrs=["10.0.0.0/8"])
        config = create_egress_proxy_config(policy)
        assert config == {}


# Integration tests (require actual Kubernetes cluster)
# Mark as integration and skip by default
@pytest.mark.integration
@pytest.mark.asyncio
class TestSandboxSpawnerIntegration:
    """Integration tests that require a real Kubernetes cluster.

    Run with: pytest tests/test_sandbox.py -v --integration
    """

    async def test_spawn_and_wait_success(self):
        """Test spawning a real pod that succeeds."""
        from factory.schemas import Task

        task = Task(
            id="integ-test-001",
            work_order_id="wo-integ-001",
            agent="test-agent",
            title="Integration test",
            instructions="Run a successful command",
        )

        spawner = SandboxSpawner()
        result = await spawner.spawn_and_wait(task)

        assert result.task_id == "integ-test-001"
        assert result.exit_code == 0
        assert result.duration_seconds > 0

    async def test_spawn_and_wait_failure(self):
        """Test spawning a pod that fails."""
        from factory.schemas import Task

        task = Task(
            id="integ-test-002",
            work_order_id="wo-integ-002",
            agent="test-agent",
            title="Integration test (failure)",
            instructions="Run a failing command",
        )

        spawner = SandboxSpawner()
        result = await spawner.spawn_and_wait(task)

        assert result.task_id == "integ-test-002"
        assert result.exit_code != 0
        assert result.error is not None
