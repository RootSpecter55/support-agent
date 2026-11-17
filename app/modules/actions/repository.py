from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_repository import BaseRepository
from app.modules.actions.models import Action


class ActionRepository(BaseRepository[Action]):
    model = Action

    async def lock(self, session: AsyncSession, action_id: UUID) -> Action | None:
        """Row-locked, so two people deciding at once cannot both win."""
        return await session.scalar(select(Action).where(Action.id == action_id).with_for_update())
