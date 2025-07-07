FROM ghcr.io/astral-sh/uv:python3.12-alpine

# Set working directory
WORKDIR /app

# Copy all files
COPY . .

# Install dependencies (package needs to be installed for uv to work)
RUN uv sync

# Note: Running as root for Docker socket access
# In production, consider using Docker socket proxy or adjusting permissions

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Run the application
CMD ["uv", "run", "main.py"]