FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Create data dir
RUN mkdir -p /app/data /tmp/anyshare-sync

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/admin/health || exit 1

CMD ["sh", "-c", "\
    python -c 'from app.models import init_db; init_db()' && \
    uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    "]
