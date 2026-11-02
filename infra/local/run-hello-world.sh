#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}/../.."

echo "==> Building hello-world sandbox image"
docker build -t factory-sandbox-hello:latest -f "${ROOT_DIR}/sandbox/Dockerfile" "${ROOT_DIR}/sandbox/"

echo ""
echo "==> Loading image into kind cluster"
kind load docker-image factory-sandbox-hello:latest --name factory

echo ""
echo "==> Deleting previous hello-world job if it exists"
kubectl delete job hello-world-sandbox -n factory-sandboxes --ignore-not-found=true

echo ""
echo "==> Running hello-world sandbox job"
kubectl apply -f "${ROOT_DIR}/infra/k8s/sandbox/hello-world-job.yaml"

echo ""
echo "==> Waiting for job to start..."
sleep 2

echo ""
echo "==> Tailing logs (will exit when job completes)"
kubectl wait --for=condition=ready pod -l job-name=hello-world-sandbox -n factory-sandboxes --timeout=30s || true
kubectl logs -n factory-sandboxes -l job-name=hello-world-sandbox --follow || true

echo ""
echo "==> Checking job status"
kubectl get job hello-world-sandbox -n factory-sandboxes

JOB_STATUS=$(kubectl get job hello-world-sandbox -n factory-sandboxes -o jsonpath='{.status.succeeded}')
if [[ "${JOB_STATUS}" == "1" ]]; then
    echo ""
    echo "✅ Hello-world sandbox completed successfully!"
    echo ""
    echo "Next steps:"
    echo "  1. View trace in Jaeger UI: http://localhost:16686"
    echo "     - Service: factory.sandbox.hello-world"
    echo "     - Look for 'hello_world_task' span"
    echo ""
    echo "  2. Verify with: ./infra/local/verify.sh"
else
    echo ""
    echo "⚠️  Job did not complete successfully. Check logs above."
    exit 1
fi
