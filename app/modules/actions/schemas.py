from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.core.schemas import ApiModel


class DecisionRequest(ApiModel):
    approver: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)


class ActionOut(ApiModel):
    id: UUID
    conversation_id: UUID
    kind: str
    input: dict[str, Any]
    status: Literal["pending", "approved", "rejected"]
    decided_by: str | None
    decision_note: str | None
    decided_at: datetime | None
    created_at: datetime


class DecisionResult(ApiModel):
    action: ActionOut
    # What the agent told the customer after the decision.
    reply: str
    conversation_status: str
