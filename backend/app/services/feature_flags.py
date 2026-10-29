"""Feature flags live in the database, not in environment variables (ADR-008).

A flag has to be visible to the people who run the office, toggleable without a deploy, and audited.
Reading it is one indexed lookup, so every guarded route can afford to check it on the request path.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

PHASE2_FLAG = "phase2.enabled"
AI_FLAG = "ai.enabled"


async def flag_enabled(session: AsyncSession, key: str) -> bool:
    from app.models.masters import FeatureFlag

    row = (await session.execute(select(FeatureFlag).where(FeatureFlag.key == key))).scalar_one_or_none()
    return bool(row and row.enabled)


async def phase2_enabled(session: AsyncSession) -> bool:
    return await flag_enabled(session, PHASE2_FLAG)


async def ai_enabled(session: AsyncSession) -> bool:
    """Transcription/summarisation must be switchable mid-day without a deploy (ADR-012)."""
    return await flag_enabled(session, AI_FLAG)
