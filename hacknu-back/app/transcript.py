"""
Meeting transcript service — store, retrieve, and summarize meeting context.

Chrome extension POSTs transcript chunks here. Agent pulls context on demand.
"""

from __future__ import annotations

import time
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.debug import debug_print
from app.models import MeetingTranscript
from app.planner import _chat_completion_token_limit_kwargs, _get_client

logger = logging.getLogger(__name__)

# ── Summary cache (per room) ──────────────────────────────────────────

_summary_cache: dict[str, tuple[str, float]] = {}  # room_id -> (summary, timestamp)
SUMMARY_TTL = 60  # seconds


async def store_chunks(
    db: AsyncSession,
    room_id: str,
    chunks: list[dict],
) -> int:
    """Store transcript chunks from extension. Returns count stored."""
    debug_print(
        "transcript.store_chunks.received",
        {
            "room_id": room_id,
            "chunks": chunks,
        },
    )
    entries = []
    for chunk in chunks:
        entry = MeetingTranscript(
            room_id=room_id,
            speaker=chunk.get("speaker", "Unknown"),
            text=chunk.get("text", ""),
        )
        entries.append(entry)
        db.add(entry)
    await db.commit()
    logger.info(f"[Transcript] Stored {len(entries)} chunks for room={room_id}")
    debug_print(
        "transcript.store_chunks.saved",
        {
            "room_id": room_id,
            "stored_count": len(entries),
        },
    )
    return len(entries)


async def get_meeting_context(
    db: AsyncSession,
    room_id: str,
    minutes: int = 15,
) -> str | None:
    """
    Get meeting context for agent consumption.

    Returns a formatted string with:
    - Cached LLM summary of the full discussion (refreshed every 60s)
    - Last 10 raw entries for recency

    Returns None if no transcript exists for this room.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    result = await db.execute(
        select(MeetingTranscript)
        .where(
            MeetingTranscript.room_id == room_id,
            MeetingTranscript.created_at >= cutoff,
        )
        .order_by(MeetingTranscript.created_at)
    )
    entries = result.scalars().all()
    debug_print(
        "transcript.get_meeting_context.raw_entries",
        {
            "room_id": room_id,
            "minutes": minutes,
            "entries": entries,
        },
    )

    if not entries:
        debug_print(
            "transcript.get_meeting_context.return_value",
            {
                "room_id": room_id,
                "context": None,
            },
        )
        return None

    # Deduplicate progressive captions (interim → final)
    entries = _dedup_progressive(entries)
    debug_print(
        "transcript.get_meeting_context.deduped_entries",
        {
            "room_id": room_id,
            "entries": entries,
        },
    )

    parts = []

    # Summary (cached)
    summary = await _get_or_create_summary(db, room_id, entries)
    if summary:
        parts.append(f"MEETING CONTEXT (summary of discussion):\n{summary}")

    # Last 10 raw entries for recency
    recent = entries[-10:]
    if recent:
        parts.append("LIVE DISCUSSION (most recent):")
        for e in recent:
            parts.append(f"  [{e.speaker}]: {e.text}")

    context = "\n".join(parts)
    debug_print(
        "transcript.get_meeting_context.return_value",
        {
            "room_id": room_id,
            "context": context,
        },
    )
    return context


def _dedup_progressive(entries: list[MeetingTranscript]) -> list[MeetingTranscript]:
    """
    Remove progressive/interim speech entries.

    Google Meet captions grow in-place: "Hello" → "Hello, how" → "Hello, how are you?"
    Each poll captures the growing text, so entry N is often a prefix of entry N+1.
    Keep only the LAST (longest) version of each progressive block.
    """
    if len(entries) <= 1:
        return entries

    result = []
    for i, entry in enumerate(entries):
        # Check if the NEXT entry (same speaker) starts with this entry's text
        if i + 1 < len(entries):
            next_entry = entries[i + 1]
            if (
                next_entry.speaker == entry.speaker
                and next_entry.text.startswith(entry.text[:20])  # fuzzy prefix match
            ):
                continue  # skip — next entry is a longer version
        result.append(entry)

    return result


async def _get_or_create_summary(
    db: AsyncSession,
    room_id: str,
    entries: list[MeetingTranscript],
) -> str | None:
    """Get cached summary or create a new one."""
    now = time.time()

    # Check cache
    if room_id in _summary_cache:
        cached_summary, cached_time = _summary_cache[room_id]
        if now - cached_time < SUMMARY_TTL:
            debug_print(
                "transcript.get_or_create_summary.cache_hit",
                {
                    "room_id": room_id,
                    "summary": cached_summary,
                },
            )
            return cached_summary

    # Need fewer than 5 entries? Skip summarization, raw is fine.
    if len(entries) < 5:
        debug_print(
            "transcript.get_or_create_summary.skipped",
            {
                "room_id": room_id,
                "entry_count": len(entries),
            },
        )
        return None

    # Build raw text
    raw_lines = [f"[{e.speaker}]: {e.text}" for e in entries]
    raw_text = "\n".join(raw_lines)
    debug_print(
        "transcript.get_or_create_summary.raw_text",
        {
            "room_id": room_id,
            "raw_text": raw_text,
        },
    )

    # Summarize
    try:
        summary = await _summarize(raw_text)
        _summary_cache[room_id] = (summary, now)
        debug_print(
            "transcript.get_or_create_summary.return_value",
            {
                "room_id": room_id,
                "summary": summary,
            },
        )
        return summary
    except Exception as e:
        logger.error(f"[Transcript] Summarization failed: {e}")
        debug_print("transcript.get_or_create_summary.error", e)
        return None


async def _summarize(raw_text: str) -> str:
    """Compress transcript into bullet points via LLM."""
    client, model = _get_client()
    system_prompt = (
        "Summarize this meeting transcript into 5-8 concise bullet points. "
        "Focus on: ideas proposed, decisions made, diagrams or flows mentioned, "
        "action items, and key topics discussed. Be concise and actionable."
    )
    request_payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {"role": "user", "content": raw_text},
        ],
        "temperature": 0.2,
        **_chat_completion_token_limit_kwargs(500),
    }
    debug_print("transcript.summarize.system_prompt", system_prompt)
    debug_print("transcript.summarize.user_input", raw_text)
    debug_print("transcript.summarize.llm_payload", request_payload)
    response = await client.chat.completions.create(**request_payload)
    debug_print("transcript.summarize.llm_response", response)
    summary = response.choices[0].message.content or ""
    debug_print("transcript.summarize.return_value", summary)
    return summary


async def get_transcript_entries(
    db: AsyncSession,
    room_id: str,
    limit: int = 100,
) -> list[dict]:
    """Get raw transcript entries for a room."""
    result = await db.execute(
        select(MeetingTranscript)
        .where(MeetingTranscript.room_id == room_id)
        .order_by(MeetingTranscript.created_at.desc())
        .limit(limit)
    )
    entries = result.scalars().all()
    payload = [
        {
            "id": str(e.id),
            "speaker": e.speaker,
            "text": e.text,
            "created_at": e.created_at.isoformat(),
        }
        for e in reversed(entries)
    ]
    debug_print(
        "transcript.get_transcript_entries.return_value",
        {
            "room_id": room_id,
            "limit": limit,
            "entries": payload,
        },
    )
    return payload


async def clear_transcript(db: AsyncSession, room_id: str) -> int:
    """Delete all transcript entries for a room. Returns count deleted."""
    result = await db.execute(
        delete(MeetingTranscript).where(MeetingTranscript.room_id == room_id)
    )
    await db.commit()

    # Clear cache
    _summary_cache.pop(room_id, None)

    count = result.rowcount
    logger.info(f"[Transcript] Cleared {count} entries for room={room_id}")
    debug_print(
        "transcript.clear_transcript.return_value",
        {
            "room_id": room_id,
            "deleted_count": count,
        },
    )
    return count
