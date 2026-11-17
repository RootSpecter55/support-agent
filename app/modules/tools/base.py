from dataclasses import dataclass, field
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ToolContext:
    """What a tool runs with. `customer_id` comes from the conversation, which the shop's
    backend created for a logged-in customer. It is never taken from the model's input, so
    no prompt can make a tool act for another customer."""

    session: AsyncSession
    customer_id: UUID
    conversation_id: UUID


@dataclass(frozen=True)
class NeedsApproval:
    """The tool did nothing yet: a person must approve first."""

    kind: str
    summary: dict[str, Any]


@dataclass(frozen=True)
class ToolOutcome:
    output: dict[str, Any] = field(default_factory=dict)
    approval: NeedsApproval | None = None
    # escalate_to_human: after this turn the conversation belongs to a person.
    escalate: bool = False


class ToolError(Exception):
    """An expected failure (unknown order, refund too large). Its message goes back to the
    model as an error result, so it can tell the customer."""


class ToolInput(BaseModel):
    # Unknown fields are refused: a model that adds "customer_id" gets a validation error.
    model_config = ConfigDict(extra="forbid")


class Tool:
    """Strategy: each tool is one class with this shape; the agent loop runs any of them the
    same way, without knowing which one it is."""

    name: ClassVar[str]
    description: ClassVar[str]
    Input: ClassVar[type[ToolInput]]

    async def run(self, ctx: ToolContext, args: Any) -> ToolOutcome:
        raise NotImplementedError

    def definition(self) -> dict[str, Any]:
        """The tool as the Messages API expects it."""
        schema = self.Input.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }
