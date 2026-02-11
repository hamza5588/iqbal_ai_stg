# RAG Vector Store Setup

## Local Development (No Docker)

**Chroma** runs in-process and needs no external services:

1. Add to `.env`:
   ```
   USE_CHROMA_LOCAL=true
   ```

2. Install ChromaDB:
   ```
   pip install chromadb
   ```

3. Run the app. Data is stored in `chroma_data/` under the project root.

---

## Milvus (Production / Docker)

This section describes how to run Milvus standalone for staging and production.

## Overview

The RAG system uses:
- **PostgreSQL** – Single source of truth for metadata (RAGThread, UserDocument)
- **Milvus** – Vector database for PDF chunk embeddings
- **HuggingFace** – Fixed embedding model (`sentence-transformers/all-MiniLM-L6-v2`, 384 dims)

## Environment Variables

```bash
MILVUS_HOST=localhost
MILVUS_PORT=19530
EMBEDDING_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_DIM=384
```

## Running Milvus Standalone (Docker)

### Option 1: Milvus standalone (single container)

```bash
docker run -d --name milvus \
  -p 19530:19530 \
  -p 9091:9091 \
  -v milvus_data:/var/lib/milvus \
  milvusdb/milvus:v2.4.0 standalone
```

### Option 2: Using docker-compose

Create `docker-compose.milvus.yml`:

```yaml
version: '3.8'
services:
  milvus:
    image: milvusdb/milvus:v2.4.0
    container_name: milvus
    command: ["milvus", "run", "standalone"]
    ports:
      - "19530:19530"
      - "9091:9091"
    volumes:
      - milvus_data:/var/lib/milvus
    environment:
      ETCD_USE_EMBED: "true"
      COMMON_STORAGETYPE: local

volumes:
  milvus_data:
```

Run: `docker-compose -f docker-compose.milvus.yml up -d`

## Database Migration

Run the RAG schema migration to add new columns:

```bash
# From project root
python -m migrations.rag_milvus_migration
```

Or let the app run it automatically on startup (via `init_db()`).

## Verify Milvus

```bash
# Check Milvus is reachable
python -c "
from pymilvus import connections
connections.connect(host='localhost', port='19530')
print('Milvus connected')
"
```

## Multi-Worker / Multi-Pod

- **PostgreSQL** – Use a shared PostgreSQL instance; each worker connects via connection pool
- **Milvus** – Use a shared Milvus cluster; no in-memory state, fully distributed
- No file-based metadata (`rag_metadata.json` removed)
- No FAISS (in-memory / on-disk index removed)

## Troubleshooting

- **Connection refused**: Ensure Milvus is running and port 19530 is open
- **Collection not found**: The collection is created automatically on first PDF ingest
- **Dimension mismatch**: Ensure `EMBEDDING_DIM=384` matches the model (`all-MiniLM-L6-v2`)
