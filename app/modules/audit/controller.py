from typing import Any
from uuid import UUID

from app.core.pagination import PageParams, page_envelope
from app.modules.audit.service import AuditService


class AuditController:
    def __init__(self, service: AuditService) -> None:
        self._service = service

    async def list(self, conversation_id: UUID | None, page: PageParams) -> dict[str, Any]:
        items, total = await self._service.list(conversation_id, page)
        return page_envelope([c.to_json() for c in items], total, page)
