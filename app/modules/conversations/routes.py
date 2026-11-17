from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.pagination import PageParams, page_params
from app.modules.conversations.controller import ConversationsController
from app.modules.conversations.schemas import CreateConversationRequest, SendMessageRequest


def conversations_router(
    controller: ConversationsController, require_client: Callable[..., Any]
) -> APIRouter:
    router = APIRouter(
        prefix="/conversations", tags=["conversations"], dependencies=[Depends(require_client)]
    )

    @router.post("", status_code=201, summary="Start a conversation for a logged-in customer")
    async def create(body: CreateConversationRequest) -> dict[str, Any]:
        return await controller.create(body)

    @router.get("/{conversation_id}", summary="Status, token use and cost")
    async def get(conversation_id: UUID) -> dict[str, Any]:
        return await controller.get(conversation_id)

    @router.get("/{conversation_id}/messages", summary="The conversation so far")
    async def messages(
        conversation_id: UUID, page: Annotated[PageParams, Depends(page_params)]
    ) -> dict[str, Any]:
        return await controller.messages(conversation_id, page)

    @router.post(
        "/{conversation_id}/messages",
        summary="Send a message; the reply streams back as Server-Sent Events",
        response_class=StreamingResponse,
    )
    async def send(conversation_id: UUID, body: SendMessageRequest) -> StreamingResponse:
        return await controller.send(conversation_id, body)

    return router
