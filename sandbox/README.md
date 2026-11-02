# Hello-World Sandbox

This directory contains a minimal sandbox image that proves:
1. Container isolation works
2. Tracing/logging hooks emit to OTel collector
3. The sandbox can be scheduled as a Kubernetes Job

## Build and Run Locally

```bash
# Build the image
docker build -t factory-sandbox-hello:latest -f sandbox/Dockerfile sandbox/

# Load into kind cluster
kind load docker-image factory-sandbox-hello:latest --name factory

# Run the hello-world job
kubectl apply -f infra/k8s/sandbox/hello-world-job.yaml

# Check logs
kubectl logs -n factory-sandboxes -l job-name=hello-world-sandbox

# View trace in Jaeger UI
# Open http://localhost:16686 and search for service "factory.sandbox.hello-world"
```

## What It Does

The hello-world sandbox:
1. Emits a span to the OTel collector with metadata
2. Logs structured output
3. Runs a trivial "task" (prints system info)
4. Exits cleanly

This validates the complete path: Pod → OTel Collector → Jaeger UI.
