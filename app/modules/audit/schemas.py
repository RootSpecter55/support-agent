from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.schemas import ApiModel


class ToolCallOut(ApiModel):
    id: UUID
    conversation_id: UUID
    tool_use_id: str
    tool_name: str
    input: dict[str, Any]
    output: dict[str, Any]
    is_error: bool
    duration_ms: int
    approver: str | None
    action_id: UUID | None
    created_at: datetime
