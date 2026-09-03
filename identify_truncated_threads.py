"""
Identify RAG threads that were silently truncated by the chunk-cap bug
(#19: `chunks = chunks[:max_chunks]` in app/utils/rag_service.py dropping
everything past RAG_STANDARD_MAX_CHUNKS/RAG_LARGE_MAX_CHUNKS with only a
log-only warning) and flag them with a persistent, user-visible warning.

Detection. Because the old per-page splitter floors chunk count at >=1 chunk
per page, an untruncated ingestion's final chunk count can never be lower
than the cap it would have hit - the cap only ever produces a stored chunk
count that is EXACTLY the cap value (`chunks[:max_chunks]` truncates to
precisely max_chunks items, barring the astronomically unlikely coincidence
of an organic document landing on that exact count). So: a thread whose
current rag_chunks count for a thread_id/user_id exactly equals one of the
known historical cap values is treated as truncated.

Historical cap values default to today's RAG_STANDARD_MAX_CHUNKS /
RAG_LARGE_MAX_CHUNKS env defaults (2000 / 1800). If those env vars were ever
set differently in production over the affected period, pass the actual
historical values with --caps (comma-separated), e.g.:

    python identify_truncated_threads.py --caps 2000,1800,1500

IMPORTANT - re-ingestion is NOT automatic. The ingestion pipeline deletes
the uploaded PDF (UPLOADED_FILES_DIR) as soon as ingestion finishes
(app/utils/rag_service.py, "Deleted uploaded PDF file"), and no other part
of this codebase retains the original bytes afterwards (UserDocument.file_path
exists in the schema but nothing currently writes to that table). There is
therefore no source file left to automatically re-ingest from. This script
identifies affected threads, records the list, and sets a persistent
ingest_warning on each thread asking the owner to re-upload the document -
re-ingestion itself happens the normal way, through the existing upload
flow, once the user does that.

Usage:
    python identify_truncated_threads.py [--caps 2000,1800] [--apply] [--out report.json]

Without --apply this only prints/records the report (dry run). With --apply
it also writes ingest_warning to the affected rag_threads rows.
"""
import argparse
import json
import logging
import os
from datetime import datetime

from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import sessionmaker

from app.config import Config
from app.models.database_models import RAGThread, RAGChunk

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_DEFAULT_CAPS = [
    int(os.getenv("RAG_STANDARD_MAX_CHUNKS", "2000")),
    int(os.getenv("RAG_LARGE_MAX_CHUNKS", "1800")),
]


def find_truncated_threads(session, caps):
    """
    Returns a list of dicts describing threads whose stored chunk count
    exactly matches one of `caps` - the fingerprint left by
    `chunks[:max_chunks]`.
    """
    caps = sorted(set(int(c) for c in caps if int(c) > 0))
    if not caps:
        return []

    counts = (
        session.query(
            RAGChunk.thread_id,
            RAGChunk.user_id,
            func.count(RAGChunk.id).label("chunk_count"),
        )
        .group_by(RAGChunk.thread_id, RAGChunk.user_id)
        .having(func.count(RAGChunk.id).in_(caps))
        .all()
    )

    if not counts:
        return []

    affected = []
    for thread_id, user_id, chunk_count in counts:
        thread = (
            session.query(RAGThread)
            .filter_by(thread_id=thread_id, user_id=user_id)
            .first()
        )
        affected.append(
            {
                "thread_id": thread_id,
                "user_id": user_id,
                "filename": getattr(thread, "filename", None),
                "num_pages": getattr(thread, "num_pages", None),
                "chunk_count": chunk_count,
                "matched_cap": chunk_count,
                "last_ingested_at": (
                    thread.last_ingested_at.isoformat()
                    if thread and thread.last_ingested_at
                    else None
                ),
            }
        )
    return affected


def apply_warnings(session, affected):
    """Set a persistent, user-facing ingest_warning on each affected thread."""
    now = datetime.utcnow()
    updated = 0
    for row in affected:
        thread = (
            session.query(RAGThread)
            .filter_by(thread_id=row["thread_id"], user_id=row["user_id"])
            .first()
        )
        if not thread:
            continue
        thread.ingest_warning = (
            f"This document was truncated during ingestion (a bug capped it at "
            f"{row['matched_cap']} chunks, silently dropping the rest) and is "
            "missing content. Please re-upload the original file to fix it."
        )
        thread.ingest_warning_at = now
        updated += 1
    session.commit()
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--caps",
        type=str,
        default=",".join(str(c) for c in _DEFAULT_CAPS),
        help="Comma-separated historical chunk-cap values to detect (default: current env defaults).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the persistent ingest_warning to affected threads (default: dry run, report only).",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=f"truncated_threads_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json",
        help="Path to write the JSON report of affected threads.",
    )
    args = parser.parse_args()
    caps = [int(c.strip()) for c in args.caps.split(",") if c.strip()]

    engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, **Config.SQLALCHEMY_ENGINE_OPTIONS)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        logger.info("Scanning for threads with chunk_count in %s ...", caps)
        affected = find_truncated_threads(session, caps)
        logger.info("Found %d likely-truncated thread(s).", len(affected))

        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "generated_at": datetime.utcnow().isoformat(),
                    "caps_checked": caps,
                    "count": len(affected),
                    "threads": affected,
                    "note": (
                        "Automatic re-ingestion was NOT performed: the original "
                        "uploaded PDF bytes are deleted immediately after "
                        "ingestion and are not retained anywhere in this "
                        "codebase. Affected threads were flagged with a "
                        "persistent ingest_warning asking the owner to "
                        "re-upload the document via the normal upload flow."
                        if args.apply
                        else "Dry run: no changes were made. Re-run with --apply "
                        "to set the persistent ingest_warning on these threads."
                    ),
                },
                f,
                indent=2,
            )
        logger.info("Report written to %s", args.out)

        if args.apply:
            updated = apply_warnings(session, affected)
            logger.info("Flagged %d thread(s) with a persistent re-upload warning.", updated)
        else:
            logger.info("Dry run - no rows modified. Re-run with --apply to flag these threads.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
