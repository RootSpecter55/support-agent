import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.db import Database
from app.core.errors import ConflictError, NotFoundError
from app.core.pagination import PageParams
from app.modules.actions.models import Action
from app.modules.actions.repository import ActionRepository
from app.modules.actions.schemas import ActionOut, DecisionResult
from app.modules.audit.service import AuditService
from app.modules.conversations.agent import AgentRunner, tool_result
from app.modules.conversations.models import ConversationStatus
from app.modules.conversations.repository import ConversationRepository
from app.modules.conversations.state import transition
from app.modules.messages.repository import MessageRepository
from app.modules.tools.base import ToolContext, ToolError
from app.modules.tools.shop_tools import IssueRefund


class ActionsService:
    def __init__(
        self,
        db: Database,
        actions: ActionRepository,
        conversations: ConversationRepository,
        messages: MessageRepository,
        audit: AuditService,
        refund_tool: IssueRefund,
        agent: AgentRunner,
    ) -> None:
        self._db = db
        self._actions = actions
        self._conversations = conversations
        self._messages = messages
        self._audit = audit
        self._refund = refund_tool
        self._agent = agent

    async def list(self, status: str | None, page: PageParams) -> tuple[list[ActionOut], int]:
        filters = [Action.status == status] if status else []
        async with self._db.transaction() as session:
            rows, total = await self._actions.list(
                session,
                *filters,
                order_by=Action.created_at,
                offset=page.offset,
                limit=page.page_size,
            )
        return [ActionOut.model_validate(a) for a in rows], total

    async def decide(
        self, action_id: UUID, *, approve: bool, approver: str, note: str | None
    ) -> DecisionResult:
        """Approving runs the refund (checked again, as the order may have changed); rejecting
        does not. Either way the outcome becomes the tool result the model was waiting for, and
        the conversation continues from there."""
        async with self._db.transaction() as session:
            action = await self._actions.lock(session, action_id)
            if action is None:
                raise NotFoundError("Action")
            if action.status != "pending":
                raise ConflictError(f"This action was already {action.status}")
            conversation = await self._conversations.lock(session, action.conversation_id)
            if conversation is None or conversation.status != ConversationStatus.AWAITING_APPROVAL:
                raise ConflictError("The conversation is not waiting for this approval")

            started = time.perf_counter()
            output: dict[str, Any]
            is_error = False
            if approve:
                ctx = ToolContext(session, conversation.customer_id, conversation.id)
                try:
                    async with session.begin_nested():
                        args = IssueRefund.Input.model_validate(action.input)
                        output = await self._refund.perform(ctx, args, approved_by=approver)
                except ToolError as exc:
                    output, is_error = {"error": str(exc)}, True
            else:
                output = {"status": "rejected", "reason": note or "Not approved by support staff"}

            await self._audit.record(
                session,
                conversation_id=conversation.id,
                tool_use_id=action.tool_use_id,
                tool_name=IssueRefund.name,
                tool_input=action.input,
                output=output,
                is_error=is_error,
                duration_ms=round((time.perf_counter() - started) * 1000),
                approver=approver,
                action_id=action.id,
            )
            # Every tool_use of that model turn is answered together, in one user turn.
            await self._messages.append(
                session,
                conversation.id,
                "user",
                [*action.held_results, tool_result(action.tool_use_id, output, is_error)],
            )
            action.status = "approved" if approve else "rejected"
            action.decided_by, action.decision_note = approver, note
            action.decided_at = datetime.now(UTC)
            transition(conversation, ConversationStatus.RESPONDING)
            decided = ActionOut.model_validate(action)

        reply: list[str] = []
        final = {"status": ConversationStatus.ACTIVE.value}

        async def collect(event: str, data: dict[str, Any]) -> None:
            if event == "text":
                reply.append(data["text"])
            elif event == "done":
                final["status"] = str(data["status"])

        await self._agent.run(decided.conversation_id, collect)
        return DecisionResult(
            action=decided, reply="".join(reply), conversation_status=final["status"]
        )
