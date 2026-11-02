#!/usr/bin/env bash
set -euo pipefail

echo "==> Verifying local factory environment"
echo ""

# Check cluster exists
echo "Checking kind cluster..."
if ! kind get clusters | grep -q "^factory$"; then
    echo "❌ Kind cluster 'factory' not found"
    echo "   Run: ./infra/local/up.sh"
    exit 1
fi
echo "✓ Kind cluster 'factory' exists"

# Check kubectl context
echo ""
echo "Checking kubectl context..."
CURRENT_CONTEXT=$(kubectl config current-context)
if [[ "${CURRENT_CONTEXT}" != "kind-factory" ]]; then
    echo "❌ Current context is ${CURRENT_CONTEXT}, expected kind-factory"
    echo "   Run: kubectl config use-context kind-factory"
    exit 1
fi
echo "✓ kubectl context is kind-factory"

# Check namespaces
echo ""
echo "Checking namespaces..."
for ns in factory-system factory-sandboxes; do
    if ! kubectl get namespace "${ns}" &>/dev/null; then
        echo "❌ Namespace ${ns} not found"
        exit 1
    fi
    echo "✓ Namespace ${ns} exists"
done

# Check core services
echo ""
echo "Checking core services..."

check_deployment() {
    local name=$1
    local namespace=$2
    
    if ! kubectl get deployment "${name}" -n "${namespace}" &>/dev/null; then
        echo "❌ Deployment ${name} not found in ${namespace}"
        return 1
    fi
    
    local ready=$(kubectl get deployment "${name}" -n "${namespace}" -o jsonpath='{.status.readyReplicas}')
    local desired=$(kubectl get deployment "${name}" -n "${namespace}" -o jsonpath='{.spec.replicas}')
    
    if [[ "${ready}" == "${desired}" ]]; then
        echo "✓ ${name} is ready (${ready}/${desired})"
        return 0
    else
        echo "⚠️  ${name} is not ready (${ready:-0}/${desired})"
        return 1
    fi
}

check_statefulset() {
    local name=$1
    local namespace=$2
    
    if ! kubectl get statefulset "${name}" -n "${namespace}" &>/dev/null; then
        echo "❌ StatefulSet ${name} not found in ${namespace}"
        return 1
    fi
    
    local ready=$(kubectl get statefulset "${name}" -n "${namespace}" -o jsonpath='{.status.readyReplicas}')
    local desired=$(kubectl get statefulset "${name}" -n "${namespace}" -o jsonpath='{.spec.replicas}')
    
    if [[ "${ready}" == "${desired}" ]]; then
        echo "✓ ${name} is ready (${ready}/${desired})"
        return 0
    else
        echo "⚠️  ${name} is not ready (${ready:-0}/${desired})"
        return 1
    fi
}

FAILURES=0

check_statefulset postgres factory-system || ((FAILURES++))
check_deployment minio factory-system || ((FAILURES++))
check_deployment otel-collector factory-system || ((FAILURES++))
check_deployment jaeger factory-system || ((FAILURES++))

# Check RuntimeClass
echo ""
echo "Checking RuntimeClass..."
if kubectl get runtimeclass gvisor &>/dev/null; then
    echo "✓ RuntimeClass 'gvisor' exists"
    echo "  Note: gVisor may not be functional without proper node configuration"
    echo "  See: infra/k8s/base/runtime-class.yaml for setup instructions"
else
    echo "⚠️  RuntimeClass 'gvisor' not found (expected if gVisor is not installed)"
fi

# Check services are accessible
echo ""
echo "Checking service endpoints..."

check_service() {
    local name=$1
    local namespace=$2
    local port=$3
    
    if ! kubectl get service "${name}" -n "${namespace}" &>/dev/null; then
        echo "❌ Service ${name} not found in ${namespace}"
        return 1
    fi
    
    echo "✓ Service ${name}:${port} exists in ${namespace}"
    return 0
}

check_service postgres factory-system 5432 || ((FAILURES++))
check_service minio factory-system 9000 || ((FAILURES++))
check_service otel-collector factory-system 4317 || ((FAILURES++))
check_service jaeger factory-system 16686 || ((FAILURES++))

echo ""
echo "========================================"
if [[ ${FAILURES} -eq 0 ]]; then
    echo "✅ All checks passed!"
    echo ""
    echo "Environment is ready. Available services:"
    echo "  - Jaeger UI: http://localhost:16686"
    echo "  - PostgreSQL: localhost:5432 (user: factory, password: factory-dev)"
    echo "  - MinIO Console: http://localhost:9001 (user: minioadmin, password: minioadmin)"
    echo ""
    echo "Next: Run the hello-world sandbox"
    echo "  ./infra/local/run-hello-world.sh"
    exit 0
else
    echo "⚠️  ${FAILURES} check(s) failed"
    echo ""
    echo "The environment may need more time to start, or there may be errors."
    echo "Check pod status with:"
    echo "  kubectl get pods -n factory-system"
    echo ""
    echo "Check logs with:"
    echo "  kubectl logs -n factory-system -l app=<service-name>"
    exit 1
fi
