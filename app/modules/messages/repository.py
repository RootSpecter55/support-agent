from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_repository import BaseRepository
from app.modules.messages.models import LlmCall, Message


class MessageRepository(BaseRepository[Message]):
    model = Message

    async def append(
        self, session: AsyncSession, conversation_id: UUID, role: str, content: list[dict[str, Any]]
    ) -> Message:
        """Adds the next turn. The caller holds the conversation row lock, so seq cannot clash."""
        last = await session.scalar(
            select(func.max(Message.seq)).where(Message.conversation_id == conversation_id)
        )
        return await self.add(
            session,
            Message(
                conversation_id=conversation_id, seq=(last or 0) + 1, role=role, content=content
            ),
        )

    async def history(self, session: AsyncSession, conversation_id: UUID) -> list[dict[str, Any]]:
        """The whole conversation in Messages API form, exactly as stored."""
        rows = await session.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.seq)
        )
        return [{"role": m.role, "content": m.content} for m in rows]


class LlmCallRepository(BaseRepository[LlmCall]):
    model = LlmCall
