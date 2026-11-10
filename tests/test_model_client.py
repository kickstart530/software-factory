"""AnthropicModelClient request shape, against a fake SDK client (no network)."""

from types import SimpleNamespace

import pytest

from factory.harness.model import (
    AnthropicModelClient,
    ExperimentConfig,
    ExperimentDefinition,
    MissingCredentialsError,
    ModelRequest,
    VariantConfig,
)


class _Block(SimpleNamespace):
    def model_dump(self, **_):
        return dict(vars(self))


class _Stream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.message


class FakeSDK:
    def __init__(self):
        self.calls = []
        msg = SimpleNamespace(
            content=[_Block(type="text", text="hi")],
            stop_reason="end_turn",
            stop_details=None,
            model="m",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=2,
                cache_read_input_tokens=5,
                cache_creation_input_tokens=0,
            ),
        )
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(stream=lambda **kw: (self.calls.append(kw), _Stream(msg))[1])
        )


def test_request_shape(model_config):
    sdk = FakeSDK()
    client = AnthropicModelClient(model_config, client=sdk)
    tool = {"name": "t", "description": "d", "input_schema": {"type": "object"}}
    resp = client.complete(
        ModelRequest("frontier", "sys", [{"role": "user", "content": "x"}], [tool])
    )
    kw = sdk.calls[0]
    assert kw["model"] == "test-model"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "low"}
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["tools"][0]["eager_input_streaming"] is True
    assert "eager_input_streaming" not in tool  # caller's definition untouched
    assert resp.text() == "hi" and resp.cache_read_tokens == 5


def test_missing_api_key(model_config, monkeypatch):
    """Missing ANTHROPIC_API_KEY raises MissingCredentialsError."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingCredentialsError) as exc_info:
        AnthropicModelClient(model_config)
    assert "ANTHROPIC_API_KEY is required" in str(exc_info.value)


def test_blank_api_key(model_config, monkeypatch):
    """Blank ANTHROPIC_API_KEY raises MissingCredentialsError."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    with pytest.raises(MissingCredentialsError) as exc_info:
        AnthropicModelClient(model_config)
    assert "ANTHROPIC_API_KEY is required" in str(exc_info.value)


def test_api_key_from_env(model_config, monkeypatch):
    """Client reads ANTHROPIC_API_KEY from the environment."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    client = AnthropicModelClient(model_config)
    assert client.client.api_key == "test-key-123"


def test_base_url_from_env(model_config, monkeypatch):
    """Client reads optional ANTHROPIC_BASE_URL from the environment."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.com")
    client = AnthropicModelClient(model_config)
    assert client.client.base_url == "https://gateway.example.com"


def test_blank_base_url_ignored(model_config, monkeypatch):
    """Blank ANTHROPIC_BASE_URL is treated as None."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "  ")
    client = AnthropicModelClient(model_config)
    # anthropic SDK's default base URL
    assert "anthropic.com" in str(client.client.base_url)


def test_ab_routing_disabled_experiment(model_config):
    """When experiment is disabled, use tier default."""
    experiments = ExperimentConfig(
        experiments={
            "test-exp": ExperimentDefinition(
                enabled=False,
                tier="frontier",
                sticky_by="work_order_id",
                variants={
                    "control": VariantConfig(weight=50, model="model-a", effort="low"),
                    "treatment": VariantConfig(weight=50, model="model-b", effort="high"),
                },
            )
        }
    )
    sdk = FakeSDK()
    client = AnthropicModelClient(model_config, client=sdk, experiments=experiments)
    resp = client.complete(
        ModelRequest(
            "frontier", "sys", [{"role": "user", "content": "x"}], [], work_order_id="wo-123"
        )
    )
    kw = sdk.calls[0]
    assert kw["model"] == "test-model"  # tier default, not experiment variant
    assert resp.experiment_id is None
    assert resp.variant is None


def test_ab_routing_sticky_assignment(model_config):
    """Sticky assignment by work_order_id produces deterministic routing."""
    experiments = ExperimentConfig(
        experiments={
            "test-exp": ExperimentDefinition(
                enabled=True,
                tier="frontier",
                sticky_by="work_order_id",
                variants={
                    "control": VariantConfig(weight=50, model="model-a", effort="low"),
                    "treatment": VariantConfig(weight=50, model="model-b", effort="high"),
                },
            )
        }
    )
    sdk = FakeSDK()
    client = AnthropicModelClient(model_config, client=sdk, experiments=experiments)

    # Same work_order_id should always get the same variant
    resp1 = client.complete(
        ModelRequest(
            "frontier", "sys", [{"role": "user", "content": "x"}], [], work_order_id="wo-123"
        )
    )
    resp2 = client.complete(
        ModelRequest(
            "frontier", "sys", [{"role": "user", "content": "y"}], [], work_order_id="wo-123"
        )
    )

    assert resp1.experiment_id == "test-exp"
    assert resp1.variant in ["control", "treatment"]
    assert resp2.variant == resp1.variant  # same variant for same work_order_id

    # Different work_order_id may get different variant
    resp3 = client.complete(
        ModelRequest(
            "frontier", "sys", [{"role": "user", "content": "z"}], [], work_order_id="wo-456"
        )
    )
    assert resp3.experiment_id == "test-exp"
    assert resp3.variant in ["control", "treatment"]


def test_ab_routing_tenant_sticky(model_config):
    """Sticky assignment by tenant_id."""
    experiments = ExperimentConfig(
        experiments={
            "test-exp": ExperimentDefinition(
                enabled=True,
                tier="frontier",
                sticky_by="tenant_id",
                variants={
                    "control": VariantConfig(weight=50, model="model-a", effort="low"),
                    "treatment": VariantConfig(weight=50, model="model-b", effort="high"),
                },
            )
        }
    )
    sdk = FakeSDK()
    client = AnthropicModelClient(model_config, client=sdk, experiments=experiments)

    resp1 = client.complete(
        ModelRequest("frontier", "sys", [{"role": "user", "content": "x"}], [], tenant_id="t-123")
    )
    resp2 = client.complete(
        ModelRequest("frontier", "sys", [{"role": "user", "content": "y"}], [], tenant_id="t-123")
    )

    assert resp1.experiment_id == "test-exp"
    assert resp1.variant in ["control", "treatment"]
    assert resp2.variant == resp1.variant  # same variant for same tenant_id


def test_ab_routing_variant_model_and_effort(model_config):
    """Experiment variants override tier model and effort."""
    experiments = ExperimentConfig(
        experiments={
            "test-exp": ExperimentDefinition(
                enabled=True,
                tier="frontier",
                sticky_by="work_order_id",
                variants={
                    "control": VariantConfig(weight=1, model="override-model", effort="medium"),
                },
            )
        }
    )
    sdk = FakeSDK()
    client = AnthropicModelClient(model_config, client=sdk, experiments=experiments)

    resp = client.complete(
        ModelRequest(
            "frontier", "sys", [{"role": "user", "content": "x"}], [], work_order_id="wo-123"
        )
    )
    kw = sdk.calls[0]
    assert kw["model"] == "override-model"
    assert kw["output_config"] == {"effort": "medium"}
    assert resp.experiment_id == "test-exp"
    assert resp.variant == "control"


def test_ab_routing_no_experiment_for_tier(model_config):
    """Request for a tier with no active experiment uses tier default."""
    experiments = ExperimentConfig(
        experiments={
            "test-exp": ExperimentDefinition(
                enabled=True,
                tier="mid",  # experiment is for mid tier
                sticky_by="work_order_id",
                variants={
                    "control": VariantConfig(weight=50, model="model-a", effort="low"),
                },
            )
        }
    )
    sdk = FakeSDK()
    client = AnthropicModelClient(model_config, client=sdk, experiments=experiments)

    resp = client.complete(
        ModelRequest(
            "frontier", "sys", [{"role": "user", "content": "x"}], [], work_order_id="wo-123"
        )
    )
    kw = sdk.calls[0]
    assert kw["model"] == "test-model"  # tier default
    assert resp.experiment_id is None
    assert resp.variant is None


def test_experiment_definition_weighted_selection():
    """ExperimentDefinition.select_variant respects weights."""
    exp = ExperimentDefinition(
        enabled=True,
        tier="frontier",
        sticky_by="work_order_id",
        variants={
            "control": VariantConfig(weight=80, model="model-a", effort="low"),
            "treatment": VariantConfig(weight=20, model="model-b", effort="high"),
        },
    )

    # Deterministic assignment based on hash
    assignments = {}
    for i in range(100):
        variant_name, _ = exp.select_variant(f"wo-{i}")
        assignments[variant_name] = assignments.get(variant_name, 0) + 1

    # With 100 samples, expect roughly 80/20 split (allowing some variance)
    assert "control" in assignments
    assert "treatment" in assignments
    assert assignments["control"] > assignments["treatment"]  # control has higher weight


def _exp(sticky_by):
    return ExperimentConfig(
        experiments={
            "e": ExperimentDefinition(
                enabled=True,
                tier="frontier",
                sticky_by=sticky_by,
                variants={
                    "a": VariantConfig(weight=50, model="model-a"),
                    "b": VariantConfig(weight=50, model="model-b"),
                },
            )
        }
    )


@pytest.mark.parametrize("sticky_by", [None, "tenant_id"])
def test_ab_variant_never_changes_within_a_work_order(model_config, sticky_by):
    """Without a usable sticky key the Work Order keeps one conversation on one model."""
    client = AnthropicModelClient(model_config, client=FakeSDK(), experiments=_exp(sticky_by))
    variants = {
        client.complete(
            ModelRequest(
                "frontier", "s", [{"role": "user", "content": "x"}], [], work_order_id="wo-1"
            )  # fmt: skip
        ).variant
        for _ in range(40)
    }
    assert len(variants) == 1


def test_ab_zero_total_weight_rejected():
    with pytest.raises(ValueError, match="sum to 0"):
        ExperimentDefinition(
            enabled=True, tier="frontier", variants={"a": VariantConfig(weight=0, model="m")}
        )
    with pytest.raises(ValueError):
        VariantConfig(weight=-1, model="m")
