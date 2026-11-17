from collections.abc import Callable
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.pagination import PageParams, page_params
from app.modules.actions.controller import ActionsController
from app.modules.actions.schemas import DecisionRequest


def actions_router(controller: ActionsController, require_staff: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/actions", tags=["actions"], dependencies=[Depends(require_staff)])

    @router.get("", summary="Actions waiting for (or after) a decision")
    async def list_actions(
        page: Annotated[PageParams, Depends(page_params)],
        status: Literal["pending", "approved", "rejected"] | None = None,
    ) -> dict[str, Any]:
        return await controller.list(status, page)

    @router.post("/{action_id}/approve", summary="Approve: run it and resume the conversation")
    async def approve(action_id: UUID, body: DecisionRequest) -> dict[str, Any]:
        return await controller.decide(action_id, body, approve=True)

    @router.post("/{action_id}/reject", summary="Reject: resume the conversation without it")
    async def reject(action_id: UUID, body: DecisionRequest) -> dict[str, Any]:
        return await controller.decide(action_id, body, approve=False)

    return router
