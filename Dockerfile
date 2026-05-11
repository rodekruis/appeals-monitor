FROM python:3.13-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock ./

# Install dependencies (--frozen ensures lock file is in sync)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY appeals_monitor /app/appeals_monitor

# Install the project itself
RUN uv sync --frozen --no-dev

# Run the pipeline
ENTRYPOINT ["uv", "run", "python", "-m", "appeals_monitor"]
