"""NetworkPolicy generation for sandbox egress control.

This module generates Kubernetes NetworkPolicy manifests to enforce egress
restrictions on sandbox pods (AD-2: bounded blast radius).
"""

from __future__ import annotations

from typing import Any

from factory.sandbox.config import EgressPolicy


def create_network_policy(
    namespace: str,
    egress_policy: EgressPolicy,
    pod_selector_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a Kubernetes NetworkPolicy manifest for sandbox egress control.
    
    Args:
        namespace: Kubernetes namespace for the NetworkPolicy
        egress_policy: Egress policy configuration
        pod_selector_labels: Labels to select sandbox pods (default: app=factory-sandbox)
        
    Returns:
        NetworkPolicy manifest as a dict
    """
    pod_selector_labels = pod_selector_labels or {"app": "factory-sandbox"}

    # Base NetworkPolicy
    network_policy: dict[str, Any] = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": "sandbox-egress-policy",
            "namespace": namespace,
        },
        "spec": {
            "podSelector": {"matchLabels": pod_selector_labels},
            "policyTypes": ["Egress"],
        },
    }

    # Build egress rules based on policy mode
    egress_rules = []

    if egress_policy.mode == "allow-all":
        # Allow all egress (not recommended for production)
        egress_rules.append({})

    elif egress_policy.mode == "deny-all":
        # Empty egress rules = deny all
        # Still need to allow DNS for Kubernetes service discovery
        egress_rules.append(
            {
                "to": [{"namespaceSelector": {"matchLabels": {"name": "kube-system"}}}],
                "ports": [{"protocol": "UDP", "port": 53}],
            }
        )

    elif egress_policy.mode == "allowlist":
        # Allow DNS first
        egress_rules.append(
            {
                "to": [{"namespaceSelector": {"matchLabels": {"name": "kube-system"}}}],
                "ports": [{"protocol": "UDP", "port": 53}],
            }
        )

        # Allow specific CIDRs
        if egress_policy.allowed_cidrs:
            for cidr in egress_policy.allowed_cidrs:
                egress_rules.append({"to": [{"ipBlock": {"cidr": cidr}}]})

        # Allow specific domains via DNS
        # Note: NetworkPolicy doesn't support domain names directly
        # In production, use a domain-aware egress proxy or service mesh
        # For now, we document the limitation and require IP allowlists
        if egress_policy.allowed_domains:
            # This is a placeholder - domains require an egress proxy
            # See factory/sandbox/README.md for egress proxy setup
            pass

    network_policy["spec"]["egress"] = egress_rules
    return network_policy


def create_egress_proxy_config(egress_policy: EgressPolicy) -> dict[str, Any]:
    """Create configuration for a domain-aware egress proxy (e.g., Squid, Envoy).
    
    NetworkPolicy alone cannot filter by domain names. For domain-based allowlists,
    an egress proxy is required.
    
    Args:
        egress_policy: Egress policy with allowed_domains
        
    Returns:
        Proxy configuration (format depends on proxy type)
    """
    if egress_policy.mode != "allowlist" or not egress_policy.allowed_domains:
        return {}

    # Example Squid ACL configuration
    squid_acl_rules = []
    for domain in egress_policy.allowed_domains:
        squid_acl_rules.append(f"acl allowed_domains dstdomain {domain}")

    return {
        "type": "squid",
        "rules": squid_acl_rules,
        "policy": [
            "http_access allow allowed_domains",
            "http_access deny all",
        ],
    }
