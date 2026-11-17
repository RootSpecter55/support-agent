"""The agent loop, written by hand: call the model with the tools, run the tools it asks for,
give it the results, and repeat until it answers or a limit is reached."""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Database
from app.core.errors import NotFoundError
from app.modules.actions.models import Action
from app.modules.actions.repository import ActionRepository
from app.modules.audit.service import AuditService
from app.modules.conversations.models import Conversation, ConversationStatus
from app.modules.conversations.repository import ConversationRepository
from app.modules.conversations.state import transition
from app.modules.messages.models import LlmCall
from app.modules.messages.repository import LlmCallRepository, MessageRepository
from app.modules.shop.repository import ShopRepository
from app.modules.tools.base import NeedsApproval, ToolContext
from app.modules.tools.registry import ToolRegistry
from app.providers.llm.base import LLMProvider, LLMTurn

logger = logging.getLogger(__name__)

# Events streamed to the client: (event name, data).
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

SYSTEM_PROMPT = """You are the customer support assistant for Northwind Goods, an online shop.

You are talking with one logged-in customer: {customer_name}. Your tools act on this customer's \
account only. You cannot see or change anyone else's orders; if asked to, explain that you can \
only help with this customer's own account.

Look things up with the tools rather than guessing. Quote order numbers like ORD-1001. Refunds \
above a limit are sent to a colleague for approval; when that happens, tell the customer it is \
waiting for approval. Hand over to a person with escalate_to_human when the customer asks for \
one or you cannot solve the problem with your tools. Keep answers short and friendly."""


def tool_result(tool_use_id: str, output: dict[str, Any], is_error: bool) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(output),
    }
    if is_error:
        block["is_error"] = True
    return block


@dataclass(frozen=True)
class Pricing:
    input_usd_per_mtok: float
    output_usd_per_mtok: float

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        total = (
            Decimal(str(self.input_usd_per_mtok)) * input_tokens
            + Decimal(str(self.output_usd_per_mtok)) * output_tokens
        )
        return (total / Decimal(1_000_000)).quantize(Decimal("0.000001"))


class AgentRunner:
    def __init__(
        self,
        db: Database,
        llm: LLMProvider,
        tools: ToolRegistry,
        conversations: ConversationRepository,
        messages: MessageRepository,
        llm_calls: LlmCallRepository,
        actions: ActionRepository,
        shop: ShopRepository,
        audit: AuditService,
        *,
        max_tool_rounds: int,
        pricing: Pricing,
    ) -> None:
        self._db = db
        self._llm = llm
        self._tools = tools
        self._conversations = conversations
        self._messages = messages
        self._llm_calls = llm_calls
        self._actions = actions
        self._shop = shop
        self._audit = audit
        self._max_rounds = max_tool_rounds
        self._pricing = pricing

    async def run(self, conversation_id: UUID, emit: Emit) -> None:
        """Runs one agent turn. The conversation must be RESPONDING with the customer's message
        (or tool results) already stored. Always leaves it in a resting state."""
        try:
            final_status = await self._loop(conversation_id, emit)
        except Exception:
            logger.exception("Agent turn failed for conversation %s", conversation_id)
            await emit("error", {"code": "AGENT_ERROR", "message": "Something went wrong"})
            final_status = ConversationStatus.ACTIVE
        async with self._db.transaction() as session:
            conversation = await self._conversations.lock(session, conversation_id)
            if conversation and conversation.status == ConversationStatus.RESPONDING:
                transition(conversation, final_status)
        await emit("done", {"status": final_status})

    async def _loop(self, conversation_id: UUID, emit: Emit) -> ConversationStatus:
        rounds = 0
        escalated = False
        rest = ConversationStatus.ACTIVE
        while True:
            async with self._db.transaction() as session:
                conversation = await self._load(session, conversation_id)
                if conversation.tokens_used >= conversation.token_budget:
                    await emit("stopped", {"reason": "token_budget_exhausted"})
                    return rest
                customer = await self._shop.customer(session, conversation.customer_id)
                history = await self._messages.history(session, conversation_id)

            async def on_text(text: str) -> None:
                await emit("text", {"text": text})

            turn = await self._llm.respond(
                system=SYSTEM_PROMPT.format(customer_name=customer.name if customer else "unknown"),
                tools=self._tools.definitions(),
                messages=history,
                on_text=on_text,
            )

            tool_uses = [b for b in turn.content if b.get("type") == "tool_use"]
            async with self._db.transaction() as session:
                conversation = await self._load(session, conversation_id)
                await self._messages.append(session, conversation_id, "assistant", turn.content)
                self._record_usage(session, conversation, turn)
                await session.flush()

                if turn.stop_reason in ("refusal", "max_tokens") or not tool_uses:
                    if tool_uses:
                        # Every tool_use needs an answer before the next request: refuse them all.
                        await self._answer_all(
                            session, conversation_id, tool_uses, "Not run: the response was cut off"
                        )
                    if turn.stop_reason in ("refusal", "max_tokens"):
                        await emit("stopped", {"reason": turn.stop_reason})
                    return ConversationStatus.ESCALATED if escalated else rest

                rounds += 1
                if rounds > self._max_rounds:
                    await self._answer_all(
                        session, conversation_id, tool_uses, "Not run: tool round limit reached"
                    )
                    await emit("stopped", {"reason": "tool_round_limit"})
                    return ConversationStatus.ESCALATED if escalated else rest

                results, pending, escalate = await self._run_tools(
                    session, conversation, tool_uses, emit
                )
                escalated = escalated or escalate
                if pending is not None:
                    tool_use, approval = pending
                    action = await self._actions.add(
                        session,
                        Action(
                            conversation_id=conversation_id,
                            tool_use_id=tool_use["id"],
                            kind=approval.kind,
                            input=tool_use["input"],
                            held_results=results,
                        ),
                    )
                    await emit(
                        "approval_required", {"actionId": str(action.id), **approval.summary}
                    )
                    return ConversationStatus.AWAITING_APPROVAL
                await self._messages.append(session, conversation_id, "user", results)

    async def _run_tools(
        self,
        session: AsyncSession,
        conversation: Conversation,
        tool_uses: list[dict[str, Any]],
        emit: Emit,
    ) -> tuple[list[dict[str, Any]], tuple[dict[str, Any], NeedsApproval] | None, bool]:
        """Runs every tool call of one model turn. Returns the tool_result blocks, the call
        waiting for approval (at most one), and whether the conversation is being escalated."""
        ctx = ToolContext(session, conversation.customer_id, conversation.id)
        results: list[dict[str, Any]] = []
        pending: tuple[dict[str, Any], NeedsApproval] | None = None
        escalate = False
        for tool_use in tool_uses:
            # A savepoint per tool: a failing tool rolls back only its own writes.
            async with session.begin_nested():
                execution = await self._tools.execute(tool_use["name"], tool_use.get("input"), ctx)
            outcome, is_error = execution.outcome, execution.is_error

            if outcome.approval is None:
                output = outcome.output
                results.append(tool_result(tool_use["id"], output, is_error))
            elif pending is None:
                # Its result comes later, when a person has decided.
                pending = (tool_use, outcome.approval)
                output = {"pendingApproval": True, **outcome.approval.summary}
            else:
                output = {"error": "Only one refund can wait for approval at a time"}
                is_error = True
                results.append(tool_result(tool_use["id"], output, is_error))

            escalate = escalate or (outcome.escalate and not is_error)
            raw_input = tool_use.get("input")
            await self._audit.record(
                session,
                conversation_id=conversation.id,
                tool_use_id=tool_use["id"],
                tool_name=tool_use["name"],
                tool_input=raw_input if isinstance(raw_input, dict) else {"raw": str(raw_input)},
                output=output,
                is_error=is_error,
                duration_ms=execution.duration_ms,
            )
            await emit(
                "tool",
                {
                    "name": tool_use["name"],
                    "isError": is_error,
                    "pendingApproval": outcome.approval is not None and not is_error,
                },
            )
        return results, pending, escalate

    async def _answer_all(
        self,
        session: AsyncSession,
        conversation_id: UUID,
        tool_uses: list[dict[str, Any]],
        reason: str,
    ) -> None:
        results = [tool_result(t["id"], {"error": reason}, True) for t in tool_uses]
        await self._messages.append(session, conversation_id, "user", results)

    def _record_usage(
        self, session: AsyncSession, conversation: Conversation, turn: LLMTurn
    ) -> None:
        cost = self._pricing.cost(turn.input_tokens, turn.output_tokens)
        conversation.input_tokens += turn.input_tokens
        conversation.output_tokens += turn.output_tokens
        conversation.cost_usd += cost
        session.add(
            LlmCall(
                conversation_id=conversation.id,
                model=turn.model,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                cost_usd=cost,
                stop_reason=turn.stop_reason,
            )
        )

    async def _load(self, session: AsyncSession, conversation_id: UUID) -> Conversation:
        conversation = await self._conversations.lock(session, conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation")
        return conversation
