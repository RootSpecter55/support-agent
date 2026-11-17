from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.schemas import ApiModel


class CreateConversationRequest(ApiModel):
    # Set by the shop's backend from its own login session, never by the customer.
    customer_id: UUID


class SendMessageRequest(ApiModel):
    content: str = Field(min_length=1, max_length=4000)


class ConversationOut(ApiModel):
    id: UUID
    customer_id: UUID
    status: str
    input_tokens: int
    output_tokens: int
    token_budget: int
    cost_usd: Decimal
    created_at: datetime


class MessageOut(ApiModel):
    seq: int
    role: str
    content: list[dict[str, Any]]
    created_at: datetime
