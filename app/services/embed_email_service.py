"""Email notifications for embed chatbot escalations and exports."""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Optional

from flask_mail import Message

from app import mail
from app.models.database_models import EmbedClient, EmbedConversation, EmbedMessage
from app.services.embed_service import get_messages

logger = logging.getLogger(__name__)


def _format_transcript(messages: list[EmbedMessage]) -> str:
    lines = []
    for m in messages:
        label = "Visitor" if m.role == "user" else "AI"
        if m.role == "system":
            label = "System"
        ts = m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else ""
        lines.append(f"[{ts}] {label}: {m.content}")
    return "\n".join(lines) if lines else "(no messages)"


def _transcript_html(messages: list[EmbedMessage]) -> str:
    parts = []
    for m in messages:
        label = "Visitor" if m.role == "user" else "AI"
        ts = m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else ""
        safe = (m.content or "").replace("<", "&lt;").replace(">", "&gt;")
        parts.append(f"<p><strong>[{ts}] {label}:</strong> {safe}</p>")
    return "\n".join(parts) if parts else "<p>(no messages)</p>"


def send_escalation_email(
    client: EmbedClient,
    conversation: EmbedConversation,
    messages: list[EmbedMessage],
    reason: str = "Visitor needs attention",
) -> bool:
    if not client.owner_email:
        return False
    try:
        contact_lines = []
        if conversation.visitor_email:
            contact_lines.append(f"Visitor email: {conversation.visitor_email}")
        if conversation.visitor_phone:
            contact_lines.append(f"Visitor phone: {conversation.visitor_phone}")
        contact_block = "\n".join(contact_lines)
        body = (
            f"{reason}\nClient: {client.client_slug}\n"
            f"Conversation ID: {conversation.id}\n"
            f"{contact_block}\n\n--- Chat transcript ---\n{_format_transcript(messages)}"
            if contact_block
            else f"{reason}\n\n--- Chat transcript ---\n{_format_transcript(messages)}"
        )
        msg = Message(
            subject=f"[Iqbal AI] {reason} — {client.client_slug}",
            recipients=[client.owner_email],
            body=body,
            html=(
                f"<h2>{reason}</h2>"
                f"<p><strong>Client:</strong> {client.client_slug}</p>"
                f"<p><strong>Conversation ID:</strong> {conversation.id}</p>"
                f"<p><strong>Visitor email:</strong> {conversation.visitor_email or '—'}</p>"
                f"<p><strong>Visitor phone:</strong> {conversation.visitor_phone or '—'}</p>"
                f"<hr><h3>Chat transcript</h3>{_transcript_html(messages)}"
            ),
        )
        mail.send(msg)
        return True
    except Exception as exc:
        logger.error("Failed to send escalation email: %s", exc, exc_info=True)
        return False


def send_export_email(
    client: EmbedClient,
    conversations: list[EmbedConversation],
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
) -> bool:
    if not client.owner_email:
        return False
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["conversation_id", "visitor_id", "created_at", "role", "channel", "content"])
    for conv in conversations:
        for m in get_messages(conv.id):
            writer.writerow([
                conv.id, conv.visitor_id,
                m.created_at.isoformat() if m.created_at else "",
                m.role, m.channel, m.content,
            ])
    date_note = f" ({from_date or 'start'} to {to_date or 'now'})" if (from_date or to_date) else ""
    try:
        msg = Message(
            subject=f"[Iqbal AI] Chat export — {client.client_slug}{date_note}",
            recipients=[client.owner_email],
            body=f"Attached: all embed chats for {client.client_slug}. Conversations: {len(conversations)}",
        )
        msg.attach(f"embed_chats_{client.client_slug}.csv", "text/csv", buf.getvalue())
        mail.send(msg)
        return True
    except Exception as exc:
        logger.error("Failed to send export email: %s", exc, exc_info=True)
        return False
