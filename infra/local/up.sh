#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="${SCRIPT_DIR}/../k8s"

echo "==> Creating kind cluster 'factory'"
kind create cluster --config="${SCRIPT_DIR}/kind-config.yaml" || {
    echo "⚠️  Cluster might already exist. Checking..."
    if kind get clusters | grep -q "^factory$"; then
        echo "✓ Cluster 'factory' already exists"
    else
        exit 1
    fi
}

echo ""
echo "==> Setting kubectl context"
kubectl config use-context kind-factory

echo ""
echo "==> Creating namespaces"
kubectl apply -f "${K8S_DIR}/base/namespaces.yaml"

echo ""
echo "==> Deploying base infrastructure"
kubectl apply -f "${K8S_DIR}/base/"

echo ""
echo "==> Waiting for PostgreSQL to be ready"
kubectl wait --for=condition=ready pod -l app=postgres -n factory-system --timeout=120s || true

echo ""
echo "==> Waiting for MinIO to be ready"
kubectl wait --for=condition=ready pod -l app=minio -n factory-system --timeout=120s || true

echo ""
echo "==> Waiting for OTel collector to be ready"
kubectl wait --for=condition=ready pod -l app=otel-collector -n factory-system --timeout=120s || true

echo ""
echo "==> Waiting for Jaeger to be ready"
kubectl wait --for=condition=ready pod -l app=jaeger -n factory-system --timeout=120s || true

echo ""
echo "✅ Local factory environment is up!"
echo ""
echo "Available services:"
echo "  - Jaeger UI: http://localhost:16686"
echo "  - PostgreSQL: localhost:5432 (user: factory, password: factory-dev, database: factory)"
echo "  - MinIO Console: http://localhost:9001 (user: minioadmin, password: minioadmin)"
echo "  - OTel Collector: localhost:4317 (gRPC)"
echo ""
echo "Next steps:"
echo "  1. Verify: ./infra/local/verify.sh"
echo "  2. Run hello-world: ./infra/local/run-hello-world.sh"
