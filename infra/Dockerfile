# JennMesh Dashboard Container
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml VERSION ./
COPY src/ src/

RUN pip install --no-cache-dir ".[dashboard]"

# Create data directory
RUN mkdir -p /data

EXPOSE 8002

CMD ["uvicorn", "jenn_mesh.dashboard.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8002"]
