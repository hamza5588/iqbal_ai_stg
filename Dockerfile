FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    libreoffice \
    ffmpeg \
    espeak-ng \
    && rm -rf /var/lib/apt/lists/*

ENV HF_HUB_DISABLE_XET=1
ENV TQDM_DISABLE=1
ENV TOKENIZERS_PARALLELISM=false
ENV PIP_DEFAULT_TIMEOUT=120

COPY requirements.txt .

# Install CPU-only torch first so sentence-transformers does not pull CUDA wheels
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir tiktoken flask-wtf sentence-transformers langchain-huggingface

COPY . .

ENV FLASK_APP=run.py
ENV FLASK_ENV=production
ENV FLASK_DEBUG=0
ENV PYTHONPATH=/app

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "9", "--threads", "8", "--worker-class", "gthread", "--timeout", "120", "--graceful-timeout", "30", "--keep-alive", "5", "run:app"]