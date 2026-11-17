from typing import Any
from uuid import UUID

from fastapi.responses import StreamingResponse

from app.core.pagination import PageParams, data_envelope, page_envelope
from app.modules.conversations.schemas import CreateConversationRequest, SendMessageRequest
from app.modules.conversations.service import ConversationsService


class ConversationsController:
    def __init__(self, service: ConversationsService) -> None:
        self._service = service

    async def create(self, body: CreateConversationRequest) -> dict[str, Any]:
        return data_envelope((await self._service.create(body.customer_id)).to_json())

    async def get(self, conversation_id: UUID) -> dict[str, Any]:
        return data_envelope((await self._service.get(conversation_id)).to_json())

    async def messages(self, conversation_id: UUID, page: PageParams) -> dict[str, Any]:
        items, total = await self._service.messages(conversation_id, page)
        return page_envelope([m.to_json() for m in items], total, page)

    async def send(self, conversation_id: UUID, body: SendMessageRequest) -> StreamingResponse:
        await self._service.start_turn(conversation_id, body.content)
        return StreamingResponse(
            self._service.stream_turn(conversation_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
