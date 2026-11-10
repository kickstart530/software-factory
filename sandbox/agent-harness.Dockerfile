# Image for sandbox pods: runs `factory sandbox-entry`, which clones the task's
# repository, runs the agent harness, and prints the TaskResult for the spawner.
# Build from the repo root:
#   docker build -f sandbox/agent-harness.Dockerfile -t software-factory/agent-harness:latest .
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md ./
COPY factory/ ./factory/
COPY evals/ ./evals/
COPY github_app/ ./github_app/
COPY agents/ ./agents/
COPY config/ ./config/
COPY schemas/ ./schemas/
RUN pip install --no-cache-dir . && rm -rf /src

# Matches the spawner's securityContext (runAsUser/runAsGroup 1000)
RUN useradd -m -u 1000 -s /bin/bash sandbox \
    && mkdir -p /workspace && chown sandbox:sandbox /workspace
USER sandbox
WORKDIR /workspace

ENTRYPOINT ["factory", "sandbox-entry"]
