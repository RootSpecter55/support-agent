from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_repository import BaseRepository
from app.modules.conversations.models import Conversation


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation

    async def lock(self, session: AsyncSession, conversation_id: UUID) -> Conversation | None:
        """Row-locked for the transaction, so status changes cannot interleave."""
        return await session.scalar(
            select(Conversation).where(Conversation.id == conversation_id).with_for_update()
        )
