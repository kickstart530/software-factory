FROM python:3.12-slim

# Install minimal dependencies for tracing
RUN pip install --no-cache-dir opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc

# Create non-root user for sandbox execution
RUN useradd -m -u 1000 -s /bin/bash sandbox

# Set up workspace directory
RUN mkdir -p /workspace && chown sandbox:sandbox /workspace

WORKDIR /workspace

# Copy the hello-world script
COPY hello-world.py /usr/local/bin/hello-world.py
RUN chmod +x /usr/local/bin/hello-world.py

# Switch to non-root user
USER sandbox

# Default command
ENTRYPOINT ["python", "/usr/local/bin/hello-world.py"]
