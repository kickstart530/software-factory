FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/kickstart530/software-factory"
LABEL org.opencontainers.image.description="Software Factory Temporal Worker"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy the project and install it as a regular (non-editable) package; agent
# definitions, model config and schemas are bundled into the wheel.
COPY pyproject.toml README.md ./
COPY factory/ ./factory/
COPY evals/ ./evals/
COPY github_app/ ./github_app/
COPY agents/ ./agents/
COPY config/ ./config/
COPY schemas/ ./schemas/
RUN pip install --no-cache-dir . && rm -rf /app/*

# Create non-root user
RUN useradd -m -u 1000 factory && \
    mkdir -p /var/lib/factory && chown factory:factory /var/lib/factory

USER factory

# /health/live and /health/ready (factory/worker.py)
EXPOSE 8080 9090

CMD ["python", "-m", "factory.worker"]
