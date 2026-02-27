FROM 172.31.238.10/qtp/base/python:3.12.11-slim-bullseye
MAINTAINER "QTP"

LABEL maintainer="ADK Middleware"
LABEL description="OpenAI-compatible API middleware for Google ADK"

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies for python-magic
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security (before copying files)
RUN useradd --create-home --shell /bin/bash app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app ./app
COPY main.py .
COPY README.md .

# Create data directory with proper permissions
RUN mkdir -p /app/data && chown -R app:app /app

# Create entrypoint script to handle permissions
RUN echo '#!/bin/sh\n\
# Fix data directory permissions if needed\n\
if [ -d /app/data ]; then\n\
    # Try to create a test file to check writability\n\
    if ! touch /app/data/.test 2>/dev/null; then\n\
        echo "Warning: /app/data is not writable, using /home/app/data"\n\
        mkdir -p /home/app/data\n\
        export DATABASE_PATH=/home/app/data/sessions.db\n\
    else\n\
        rm -f /app/data/.test\n\
    fi\n\
fi\n\
\n\
# Run the application\n\
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info\n\
' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh && chown app:app /app/entrypoint.sh

# Switch to non-root user
USER app

# Expose port (container listens on 8000)
EXPOSE 8000

# Health check - use start-period to give app time to initialize
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/v1/health || exit 1

# Run the application via entrypoint
CMD ["/app/entrypoint.sh"]
