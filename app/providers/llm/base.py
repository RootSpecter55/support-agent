from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

OnText = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class LLMTurn:
    """One model response, in a provider-neutral shape. `content` holds the Messages API
    content blocks as plain dicts (text, tool_use, thinking, ...). They are stored and sent
    back unchanged on the next request, which the API requires for thinking blocks."""

    content: list[dict[str, Any]]
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    model: str


class LLMProvider(Protocol):
    """One call to the model. Implementations: Anthropic API, Amazon Bedrock, a rule-based
    fake for local use, and a scripted fake for tests. The agent loop sees only this."""

    name: str

    async def respond(
        self,
        *,
        system: str,
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        on_text: OnText,
    ) -> LLMTurn:
        """Streams text deltas to `on_text` and returns the complete turn."""
        ...
