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

# Copy dependency files
COPY pyproject.toml ./
COPY README.md ./

# Copy application code
COPY factory/ ./factory/
COPY agents/ ./agents/
COPY config/ ./config/
COPY schemas/ ./schemas/

# Install Python dependencies
# Note: Add 'temporal' extra to pyproject.toml for Temporal SDK
RUN pip install --no-cache-dir -e ".[dev]"

# Create non-root user
RUN useradd -m -u 1000 factory && \
    chown -R factory:factory /app && \
    mkdir -p /tmp && chmod 1777 /tmp

USER factory

# Health check endpoint (implement in factory/worker.py)
EXPOSE 8080 9090

# Entry point for Temporal worker
# The worker implementation will be added in Phase 2
CMD ["python", "-m", "factory.worker"]
