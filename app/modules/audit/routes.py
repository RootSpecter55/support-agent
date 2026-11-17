from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.pagination import PageParams, page_params
from app.modules.audit.controller import AuditController


def audit_router(controller: AuditController, require_staff: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_staff)])

    @router.get("/tool-calls", summary="Every tool call, oldest first")
    async def tool_calls(
        page: Annotated[PageParams, Depends(page_params)],
        conversation_id: Annotated[UUID | None, Query(alias="conversationId")] = None,
    ) -> dict[str, Any]:
        return await controller.list(conversation_id, page)

    return router
