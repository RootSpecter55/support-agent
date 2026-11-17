from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Database
from app.core.pagination import PageParams
from app.modules.audit.models import ToolCall
from app.modules.audit.repository import ToolCallRepository
from app.modules.audit.schemas import ToolCallOut


class AuditService:
    def __init__(self, db: Database, calls: ToolCallRepository) -> None:
        self._db = db
        self._calls = calls

    async def record(
        self,
        session: AsyncSession,
        *,
        conversation_id: UUID,
        tool_use_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        output: dict[str, Any],
        is_error: bool,
        duration_ms: int,
        approver: str | None = None,
        action_id: UUID | None = None,
    ) -> None:
        """Called inside the same transaction as the tool's own writes: no write without its
        audit row, and no audit row for a write that rolled back."""
        await self._calls.add(
            session,
            ToolCall(
                conversation_id=conversation_id,
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                input=tool_input,
                output=output,
                is_error=is_error,
                duration_ms=duration_ms,
                approver=approver,
                action_id=action_id,
            ),
        )

    async def list(
        self, conversation_id: UUID | None, page: PageParams
    ) -> tuple[list[ToolCallOut], int]:
        filters = [ToolCall.conversation_id == conversation_id] if conversation_id else []
        async with self._db.transaction() as session:
            rows, total = await self._calls.list(
                session,
                *filters,
                order_by=ToolCall.created_at,
                offset=page.offset,
                limit=page.page_size,
            )
        return [ToolCallOut.model_validate(r) for r in rows], total
