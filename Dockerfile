FROM python:3.13-slim

# Install system dependencies
# libgl1-mesa-glx: OpenGL + transitive X11/xcb libs (needed by pypdfium2/docling)
# libglib2.0-0:   GLib (needed by document processing pipeline)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        libgl1-mesa-glx \
        libglib2.0-0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock README.md ./

# Install dependencies (--frozen ensures lock file is in sync)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY appeals_monitor /app/appeals_monitor

# Install the project itself
RUN uv sync --frozen --no-dev

# Ensure Python output is sent straight to logs (no buffering)
ENV PYTHONUNBUFFERED=1

# Run the pipeline
ENTRYPOINT ["uv", "run", "python", "-m", "appeals_monitor"]
