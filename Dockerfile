FROM python:3.11

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    libreoffice \
    && rm -rf /var/lib/apt/lists/*
    RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Add this to avoid hf_xet thread panic
ENV HF_HUB_DISABLE_XET=1
# Disable tqdm threading and tokenizer parallelism to prevent "cannot start new thread" errors
ENV TQDM_DISABLE=1
ENV TOKENIZERS_PARALLELISM=false

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt \
    tiktoken \
    flask_wtf \
    sentence-transformers \
    langchain-huggingface

# Copy application files
COPY . .

# Set environment variables
ENV FLASK_APP=run.py
ENV FLASK_ENV=production
ENV FLASK_DEBUG=0
ENV PYTHONPATH=/app

# Expose port
EXPOSE 5000

# Run the app
# Increased timeout to 1800s (30 minutes) for streaming endpoints
# With 16 CPUs: Use 4 workers per Flask app = 16 workers total across 4 apps
# graceful-timeout allows workers to finish current requests before being killed
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--threads", "2", "--timeout", "1800", "--graceful-timeout", "30", "--keep-alive", "5", "--preload", "run:app"]
