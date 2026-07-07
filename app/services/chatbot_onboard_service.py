"""
Admin chatbot onboarding orchestration.

Automates the manual workflow documented in docs/LMDA_EMBED_PRODUCTION_GUIDE.md by
reusing the same building blocks as the CLI (scripts/embed_onboard.py) and consultant
upload route (app/routes/consultant_routes.py ingest).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from app.config import Config
from app.models.database_models import EmbedClient, RAGChunk, RAGThread
from app.services.embed_service import (
    create_embed_client,
    get_client_by_slug,
    parse_allowed_origins,
)
from app.utils.db import get_db
from app.utils.origin_whitelist import (
    add_origins_to_global_whitelist,
    generate_client_slug_from_url,
    origins_from_website_url,
    validate_and_normalize_website_url,
    validate_owner_email,
)
from app.utils.rag_service import delete_thread, ingest_pdf, warmup_rag_embeddings

logger = logging.getLogger(__name__)


class OnboardError(Exception):
    """Raised when onboarding validation or a pipeline step fails."""

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.code = code or "ONBOARD_ERROR"


def build_integration_script(api_base: str, client_key: str) -> str:
    """Same snippet format as docs/LMDA_EMBED_PRODUCTION_GUIDE.md Step E."""
    base = (api_base or "").rstrip("/")
    return (
        f'<script src="{base}/static/js/consultant-embed.js"></script>\n'
        f"<script>\n"
        f"  IqbalConsultant.init({{\n"
        f'    apiBase: "{base}",\n'
        f'    clientKey: "{client_key}"\n'
        f"  }});\n"
        f"</script>"
    )


def _save_consultant_thread(user_id: int, thread_id: str, filename: str, result: dict | None = None) -> None:
    """Mirror consultant_routes._save_thread — persists RAGThread for consultant ingest."""
    db = get_db()
    row = db.query(RAGThread).filter_by(thread_id=thread_id).first()
    now = datetime.utcnow()
    if not row:
        row = RAGThread(
            user_id=user_id,
            thread_id=thread_id,
            name=f"Consultant {now.strftime('%Y-%m-%d %H:%M')}",
            filename=filename,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

    if result:
        row.filename = filename
        row.has_document = True
        row.num_pages = result.get("num_pages")
        row.last_ingested_at = now
        row.embedding_model = result.get("embedding_model")
        row.updated_at = now
        db.commit()


def ingest_consultant_pdf(user_id: int, file_bytes: bytes, filename: str) -> dict:
    """
    Reuse the consultant /ingest pipeline: ingest_pdf() + RAGThread persistence.
    Returns thread_id and ingest metadata.
    """
    if not file_bytes:
        raise OnboardError("PDF file is empty", "INVALID_PDF")
    if not filename or not filename.lower().endswith(".pdf"):
        raise OnboardError("Only PDF files are supported", "INVALID_PDF")

    session_id = str(uuid.uuid4())[:8]
    thread_id = f"user_{user_id}_consultant_{session_id}"

    warmup_rag_embeddings()
    result = ingest_pdf(
        file_bytes=file_bytes,
        thread_id=thread_id,
        filename=filename,
        progress_callback=None,
        user_id=user_id,
    )
    _save_consultant_thread(user_id, thread_id, filename, result)

    logger.info(
        "Admin onboard ingest: user=%s thread=%s file=%s pages=%s chunks=%s",
        user_id,
        thread_id,
        filename,
        result.get("num_pages"),
        result.get("chunks"),
    )
    return {
        "thread_id": thread_id,
        "user_id": user_id,
        "filename": result.get("filename", filename),
        "num_pages": result.get("num_pages", 0),
        "chunks": result.get("chunks", 0),
    }


def _rollback_rag_thread(user_id: int, thread_id: str) -> None:
    db = get_db()
    try:
        delete_thread(thread_id)
    except Exception as exc:
        logger.warning("Rollback: vector cleanup failed for %s: %s", thread_id, exc)
    try:
        db.query(RAGChunk).filter_by(thread_id=thread_id).delete()
        row = db.query(RAGThread).filter_by(thread_id=thread_id, user_id=user_id).first()
        if row:
            db.delete(row)
        db.commit()
    except Exception as exc:
        logger.warning("Rollback: DB cleanup failed for %s: %s", thread_id, exc)
        db.rollback()


def _rollback_embed_client(client_id: int) -> None:
    db = get_db()
    try:
        client = db.query(EmbedClient).filter_by(id=client_id).first()
        if client:
            db.delete(client)
            db.commit()
    except Exception as exc:
        logger.warning("Rollback: embed client delete failed id=%s: %s", client_id, exc)
        db.rollback()


def _check_duplicates(client_slug: str, owner_email: str, origins: list[str]) -> None:
    db = get_db()
    if get_client_by_slug(client_slug):
        raise OnboardError(
            f"A chatbot client with slug '{client_slug}' already exists for this website",
            "DUPLICATE_WEBSITE",
        )

    owner_match = (
        db.query(EmbedClient)
        .filter(EmbedClient.owner_email == owner_email, EmbedClient.active.is_(True))
        .first()
    )
    if owner_match:
        raise OnboardError(
            f"An active embed client already uses owner email '{owner_email}'",
            "DUPLICATE_OWNER",
        )

    origin_set = set(origins)
    for client in db.query(EmbedClient).filter(EmbedClient.active.is_(True)).all():
        client_origins = set(parse_allowed_origins(client.allowed_origins))
        overlap = origin_set & client_origins
        if overlap:
            raise OnboardError(
                f"Website origin already registered to client '{client.client_slug}': {sorted(overlap)[0]}",
                "DUPLICATE_WEBSITE",
            )


def onboard_chatbot(
    admin_user_id: int,
    file_bytes: bytes,
    filename: str,
    owner_email: str,
    website_url: str,
) -> dict:
    """
    Full admin onboarding pipeline with rollback on failure.

    Steps (same outcome as manual upload + embed_onboard.py + whitelist edit):
      1. Validate inputs and check duplicates
      2. Upload PDF via consultant ingest pipeline
      3. Create embed client via embed_service.create_embed_client (CLI logic)
      4. Whitelist website origins globally and per-client
    """
    email = validate_owner_email(owner_email)
    normalized_url = validate_and_normalize_website_url(website_url)
    origins = origins_from_website_url(normalized_url)
    client_slug = generate_client_slug_from_url(normalized_url)

    _check_duplicates(client_slug, email, origins)

    thread_id: Optional[str] = None
    embed_client_id: Optional[int] = None
    client_secret: Optional[str] = None

    try:
        ingest_result = ingest_consultant_pdf(admin_user_id, file_bytes, filename)
        thread_id = ingest_result["thread_id"]
        service_user_id = ingest_result["user_id"]

        try:
            client, client_secret = create_embed_client(
                client_slug=client_slug,
                owner_email=email,
                allowed_origins=origins,
                rag_thread_id=thread_id,
                service_user_id=service_user_id,
            )
            embed_client_id = client.id

            try:
                add_origins_to_global_whitelist(origins)
            except Exception as exc:
                _rollback_embed_client(embed_client_id)
                embed_client_id = None
                raise OnboardError(
                    f"Failed to update global whitelist: {exc}",
                    "WHITELIST_UPDATE_FAILED",
                ) from exc

            api_base = (Config.SERVER_URL or "").rstrip("/")
            integration_script = build_integration_script(api_base, client_secret)

            return {
                "success": True,
                "client_slug": client_slug,
                "owner_email": email,
                "website_url": normalized_url,
                "allowed_origins": origins,
                "thread_id": thread_id,
                "user_id": service_user_id,
                "client_secret": client_secret,
                "integration_script": integration_script,
                "filename": ingest_result.get("filename"),
                "num_pages": ingest_result.get("num_pages"),
                "chunks": ingest_result.get("chunks"),
            }
        except OnboardError:
            raise
        except Exception as exc:
            raise OnboardError(
                f"Failed to create embed client: {exc}",
                "SECRET_GENERATION_FAILED",
            ) from exc
    except OnboardError:
        if embed_client_id is not None:
            _rollback_embed_client(embed_client_id)
        if thread_id:
            _rollback_rag_thread(admin_user_id, thread_id)
        raise
    except ValueError as exc:
        raise OnboardError(str(exc), "VALIDATION_ERROR") from exc
    except Exception as exc:
        logger.error("Chatbot onboard failed: %s", exc, exc_info=True)
        if embed_client_id is not None:
            _rollback_embed_client(embed_client_id)
        if thread_id:
            _rollback_rag_thread(admin_user_id, thread_id)
        raise OnboardError(f"Onboarding failed: {exc}", "ONBOARD_FAILED") from exc
