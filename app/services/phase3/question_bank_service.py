"""Question bank CRUD + list."""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.phase3_models import QuestionBankItem


def item_to_dict(row: QuestionBankItem) -> Dict[str, Any]:
    tags = []
    if row.tags_json:
        try:
            tags = json.loads(row.tags_json)
        except Exception:
            tags = []
    meta = {}
    if row.metadata_json:
        try:
            meta = json.loads(row.metadata_json)
        except Exception:
            meta = {}
    return {
        "id": row.id,
        "syllabus_topic_id": row.syllabus_topic_id,
        "stem": row.stem,
        "difficulty": row.difficulty,
        "bloom_level": row.bloom_level,
        "tags": tags,
        "source": row.source,
        "explanation": row.explanation,
        "metadata": meta,
        "created_by_user_id": row.created_by_user_id,
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_items(
    db: Session,
    *,
    syllabus_topic_id: Optional[int] = None,
    active_only: bool = True,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    q = db.query(QuestionBankItem)
    if active_only:
        q = q.filter(QuestionBankItem.is_active.is_(True))
    if syllabus_topic_id:
        q = q.filter(QuestionBankItem.syllabus_topic_id == syllabus_topic_id)
    rows = q.order_by(QuestionBankItem.id.desc()).limit(limit).all()
    return [item_to_dict(r) for r in rows]


def list_items_near_difficulty(
    db: Session,
    *,
    center: int,
    spread: int = 1,
    active_only: bool = True,
    limit: int = 80,
) -> List[Dict[str, Any]]:
    """Prefer questions near an adaptive difficulty band (1–5)."""
    c = max(1, min(5, int(center)))
    sp = max(0, int(spread))
    lo = max(1, c - sp)
    hi = min(5, c + sp)
    q = db.query(QuestionBankItem).filter(
        QuestionBankItem.difficulty >= lo,
        QuestionBankItem.difficulty <= hi,
    )
    if active_only:
        q = q.filter(QuestionBankItem.is_active.is_(True))
    rows = q.order_by(QuestionBankItem.id.asc()).limit(limit).all()
    return [item_to_dict(r) for r in rows]


def create_item(
    db: Session,
    *,
    stem: str,
    difficulty: int,
    bloom_level: str,
    created_by_user_id: Optional[int],
    syllabus_topic_id: Optional[int] = None,
    tags: Optional[List[str]] = None,
    source: Optional[str] = None,
    explanation: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> QuestionBankItem:
    row = QuestionBankItem(
        stem=stem,
        difficulty=difficulty,
        bloom_level=bloom_level,
        syllabus_topic_id=syllabus_topic_id,
        tags_json=json.dumps(tags or []),
        source=source,
        explanation=explanation,
        metadata_json=json.dumps(metadata or {}, default=str),
        created_by_user_id=created_by_user_id,
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_item(db: Session, *, item_id: int, **fields: Any) -> Optional[QuestionBankItem]:
    row = db.query(QuestionBankItem).filter(QuestionBankItem.id == item_id).first()
    if not row:
        return None
    for k, v in fields.items():
        if k == "tags" and v is not None:
            row.tags_json = json.dumps(v)
        elif k == "metadata" and v is not None:
            row.metadata_json = json.dumps(v, default=str)
        elif k in ("stem", "source", "explanation", "bloom_level") and v is not None:
            setattr(row, k, v)
        elif k == "difficulty" and v is not None:
            setattr(row, k, max(1, min(5, int(v))))
        elif k == "syllabus_topic_id":
            setattr(row, k, v)
        elif k == "is_active" and v is not None:
            setattr(row, k, bool(v))
    db.commit()
    db.refresh(row)
    return row


def soft_delete(db: Session, *, item_id: int) -> bool:
    row = db.query(QuestionBankItem).filter(QuestionBankItem.id == item_id).first()
    if not row:
        return False
    row.is_active = False
    db.commit()
    return True


def bulk_create_from_csv(
    db: Session,
    *,
    csv_text: str,
    created_by_user_id: int,
) -> Dict[str, Any]:
    """CSV columns: stem (required), difficulty, bloom_level, syllabus_topic_id, tags (pipe-separated)."""
    reader = csv.DictReader(io.StringIO(csv_text))
    created = 0
    errors: List[Dict[str, Any]] = []
    for i, row in enumerate(reader):
        try:
            stem = (row.get("stem") or "").strip()
            if not stem:
                raise ValueError("stem is empty")
            diff = int(row.get("difficulty") or 3)
            bloom = (row.get("bloom_level") or "understand").strip().lower()
            topic_raw = row.get("syllabus_topic_id")
            topic_id: Optional[int]
            if topic_raw in (None, "", "null"):
                topic_id = None
            else:
                topic_id = int(topic_raw)
            tags_raw = row.get("tags") or ""
            tags = [t.strip() for t in tags_raw.split("|") if t.strip()]
            create_item(
                db,
                stem=stem,
                difficulty=max(1, min(5, diff)),
                bloom_level=bloom,
                syllabus_topic_id=topic_id,
                tags=tags,
                created_by_user_id=created_by_user_id,
            )
            created += 1
        except Exception as exc:
            errors.append({"row": i + 2, "error": str(exc)})
    return {"created": created, "errors": errors}


def list_similar_concept(
    db: Session,
    *,
    syllabus_topic_id: Optional[int],
    exclude_ids: Optional[List[int]] = None,
    bloom_level: Optional[str] = None,
    tag_overlap: Optional[List[str]] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Prefer same topic, overlapping tags, same bloom; exclude given question ids.
    Falls back to same-topic any item.
    """
    exclude_ids = exclude_ids or []
    ex = set(int(x) for x in exclude_ids)
    q = db.query(QuestionBankItem).filter(QuestionBankItem.is_active.is_(True))
    if syllabus_topic_id:
        q = q.filter(QuestionBankItem.syllabus_topic_id == syllabus_topic_id)
    rows = q.order_by(QuestionBankItem.id.desc()).limit(200).all()
    tag_set = {t.lower() for t in (tag_overlap or []) if t}

    def score_row(r: QuestionBankItem) -> tuple:
        if r.id in ex:
            return (-999, 0)
        tags: List[str] = []
        if r.tags_json:
            try:
                tags = [str(x).lower() for x in json.loads(r.tags_json)]
            except Exception:
                tags = []
        overlap = len(tag_set.intersection(tags)) if tag_set else 0
        bloom_match = 1 if bloom_level and (r.bloom_level or "").lower() == bloom_level.lower() else 0
        return (overlap, bloom_match)

    ranked = sorted(rows, key=score_row, reverse=True)
    out: List[Dict[str, Any]] = []
    for r in ranked:
        if r.id in ex:
            continue
        if score_row(r)[0] < 0:
            continue
        out.append(item_to_dict(r))
        if len(out) >= limit:
            break
    if not out and syllabus_topic_id:
        return list_items(db, syllabus_topic_id=syllabus_topic_id, active_only=True, limit=limit)
    return out
