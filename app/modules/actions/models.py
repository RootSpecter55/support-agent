import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Action(Base):
    """A tool call the agent may not perform alone: a person approves or rejects it.

    `held_results` are the tool_result blocks of the other tool calls from the same model turn:
    the API needs every tool_use answered in one message, so they wait here with this one.
    """

    __tablename__ = "actions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    tool_use_id: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(30))  # "refund"
    input: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|approved|rejected
    held_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    decided_by: Mapped[str | None] = mapped_column(String(100))
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
