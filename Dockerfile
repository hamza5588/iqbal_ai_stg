

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

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--threads", "2", "--timeout", "1800", "--graceful-timeout", "30", "--keep-alive", "5", "--preload", "run:app"]