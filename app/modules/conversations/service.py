import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from app.core.db import Database
from app.core.errors import AppError, NotFoundError
from app.core.pagination import PageParams
from app.modules.conversations.agent import AgentRunner
from app.modules.conversations.models import Conversation, ConversationStatus
from app.modules.conversations.repository import ConversationRepository
from app.modules.conversations.schemas import ConversationOut, MessageOut
from app.modules.conversations.state import transition
from app.modules.messages.models import Message
from app.modules.messages.repository import MessageRepository
from app.modules.shop.repository import ShopRepository

_END = object()

BUSY_REASONS = {
    ConversationStatus.RESPONDING: "The assistant is still answering the previous message",
    ConversationStatus.AWAITING_APPROVAL: "A refund is waiting for approval by our team",
    ConversationStatus.ESCALATED: "This conversation has been handed to a person",
}


def sse(event: str, data: dict[str, Any]) -> str:
    """One Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


class ConversationsService:
    def __init__(
        self,
        db: Database,
        conversations: ConversationRepository,
        messages: MessageRepository,
        shop: ShopRepository,
        agent: AgentRunner,
        token_budget: int,
    ) -> None:
        self._db = db
        self._conversations = conversations
        self._messages = messages
        self._shop = shop
        self._agent = agent
        self._token_budget = token_budget
        # Running agent turns, kept referenced so they finish even if the client disconnects.
        self._running: set[asyncio.Task[None]] = set()

    async def create(self, customer_id: UUID) -> ConversationOut:
        async with self._db.transaction() as session:
            if await self._shop.customer(session, customer_id) is None:
                raise NotFoundError("Customer")
            conversation = await self._conversations.add(
                session, Conversation(customer_id=customer_id, token_budget=self._token_budget)
            )
        return ConversationOut.model_validate(conversation)

    async def get(self, conversation_id: UUID) -> ConversationOut:
        async with self._db.transaction() as session:
            conversation = await self._conversations.get(session, conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation")
        return ConversationOut.model_validate(conversation)

    async def messages(
        self, conversation_id: UUID, page: PageParams
    ) -> tuple[list[MessageOut], int]:
        await self.get(conversation_id)
        async with self._db.transaction() as session:
            rows, total = await self._messages.list(
                session,
                Message.conversation_id == conversation_id,
                order_by=Message.seq,
                offset=page.offset,
                limit=page.page_size,
            )
        # Thinking blocks are kept for the model but are not useful to read.
        return [
            MessageOut(
                seq=m.seq,
                role=m.role,
                content=[
                    b for b in m.content if b.get("type") not in ("thinking", "redacted_thinking")
                ],
                created_at=m.created_at,
            )
            for m in rows
        ], total

    async def start_turn(self, conversation_id: UUID, text: str) -> None:
        """Stores the customer's message and claims the conversation for one agent turn. Errors
        here (unknown conversation, busy, escalated, out of budget) are plain JSON responses."""
        async with self._db.transaction() as session:
            conversation = await self._conversations.lock(session, conversation_id)
            if conversation is None:
                raise NotFoundError("Conversation")
            if conversation.tokens_used >= conversation.token_budget:
                raise AppError(
                    409, "BUDGET_EXHAUSTED", "This conversation has used its token budget"
                )
            # Only an idle conversation takes a new message. (awaiting_approval -> responding is
            # also allowed by the state machine, but only for resuming after a decision: a new
            # message there would leave the model's pending tool call unanswered.)
            if conversation.status != ConversationStatus.ACTIVE:
                raise AppError(409, "NOT_ACCEPTING_MESSAGES", BUSY_REASONS[conversation.status])
            transition(conversation, ConversationStatus.RESPONDING)
            await self._messages.append(
                session, conversation_id, "user", [{"type": "text", "text": text}]
            )

    async def stream_turn(self, conversation_id: UUID) -> AsyncIterator[str]:
        """Runs the agent and yields its events as SSE. The agent runs in its own task, so a
        client that disconnects does not leave the conversation half-way through a turn."""
        queue: asyncio.Queue[Any] = asyncio.Queue()

        async def emit(event: str, data: dict[str, Any]) -> None:
            await queue.put(sse(event, data))

        async def run() -> None:
            try:
                await self._agent.run(conversation_id, emit)
            finally:
                await queue.put(_END)

        task = asyncio.create_task(run())
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        while (item := await queue.get()) is not _END:
            yield item
