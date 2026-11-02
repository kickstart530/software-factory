#!/usr/bin/env bash
set -euo pipefail

echo "==> Checking prerequisites for local software factory environment"

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Install Docker Desktop or Docker Engine:"
    echo "   https://docs.docker.com/get-docker/"
    exit 1
fi
echo "✓ Docker found: $(docker --version)"

# Check kind
if ! command -v kind &> /dev/null; then
    echo "⚠️  kind not found. Installing..."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64
        chmod +x ./kind
        sudo mv ./kind /usr/local/bin/kind
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        brew install kind || {
            curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-darwin-amd64
            chmod +x ./kind
            sudo mv ./kind /usr/local/bin/kind
        }
    else
        echo "❌ Unsupported OS for automatic kind installation. See: https://kind.sigs.k8s.io/docs/user/quick-start/#installation"
        exit 1
    fi
fi
echo "✓ kind found: $(kind version)"

# Check kubectl
if ! command -v kubectl &> /dev/null; then
    echo "⚠️  kubectl not found. Installing..."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
        chmod +x kubectl
        sudo mv kubectl /usr/local/bin/kubectl
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        brew install kubectl || {
            curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/darwin/amd64/kubectl"
            chmod +x kubectl
            sudo mv kubectl /usr/local/bin/kubectl
        }
    else
        echo "❌ Unsupported OS for automatic kubectl installation. See: https://kubernetes.io/docs/tasks/tools/"
        exit 1
    fi
fi
echo "✓ kubectl found: $(kubectl version --client --short 2>/dev/null || kubectl version --client)"

echo ""
echo "✅ All prerequisites satisfied"
echo ""
echo "Optional: For gVisor support (recommended), see:"
echo "  https://gvisor.dev/docs/user_guide/install/"
echo ""
echo "Next: Run './infra/local/up.sh' to create the cluster"
