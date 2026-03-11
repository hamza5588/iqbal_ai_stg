

FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    libreoffice \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV HF_HUB_DISABLE_XET=1
ENV TQDM_DISABLE=1
ENV TOKENIZERS_PARALLELISM=false

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir tiktoken flask_wtf sentence-transformers langchain-huggingface

COPY . .

ENV FLASK_APP=run.py
ENV FLASK_ENV=production
ENV FLASK_DEBUG=0
ENV PYTHONPATH=/app

EXPOSE 5000

# NOTE: we intentionally do NOT use --preload here. Preloading the app in the
# master process and then forking workers can cause PostgreSQL connections
# created during app initialization to be shared across processes, which leads
# to "lost synchronization with server" and similar psycopg2 errors under load.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "20", "--threads", "3", "--timeout", "1800", "--graceful-timeout", "10", "--keep-alive", "5", "run:app"]
