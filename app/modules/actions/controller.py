from typing import Any
from uuid import UUID

from app.core.pagination import PageParams, data_envelope, page_envelope
from app.modules.actions.schemas import DecisionRequest
from app.modules.actions.service import ActionsService


class ActionsController:
    def __init__(self, service: ActionsService) -> None:
        self._service = service

    async def list(self, status: str | None, page: PageParams) -> dict[str, Any]:
        items, total = await self._service.list(status, page)
        return page_envelope([a.to_json() for a in items], total, page)

    async def decide(self, action_id: UUID, body: DecisionRequest, approve: bool) -> dict[str, Any]:
        result = await self._service.decide(
            action_id, approve=approve, approver=body.approver, note=body.note
        )
        return data_envelope(result.to_json())
