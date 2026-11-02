#!/usr/bin/env bash
set -euo pipefail

echo "==> Deleting kind cluster 'factory'"
kind delete cluster --name factory

echo ""
echo "✅ Local factory environment has been torn down"
