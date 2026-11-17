import json
from collections.abc import Callable
from typing import Any

from app.providers.llm.base import LLMTurn, OnText

Step = Callable[[list[dict[str, Any]]], LLMTurn]


def say(text: str, stop_reason: str = "end_turn") -> Step:
    """A step that answers with text."""

    def step(_: list[dict[str, Any]]) -> LLMTurn:
        return LLMTurn(
            [{"type": "text", "text": text}], stop_reason, 100, len(text) // 4 + 1, "scripted"
        )

    return step


def call(*calls: tuple[str, dict[str, Any]], text: str = "") -> Step:
    """A step that calls one or more tools (in parallel, as one turn)."""

    def step(messages: list[dict[str, Any]]) -> LLMTurn:
        blocks: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        for n, (name, tool_input) in enumerate(calls):
            blocks.append(
                {
                    "type": "tool_use",
                    "id": f"toolu_{len(messages)}_{n}",
                    "name": name,
                    "input": tool_input,
                }
            )
        return LLMTurn(blocks, "tool_use", 100, 20, "scripted")

    return step


class ScriptedLLM:
    """A deterministic model for tests: plays its steps in order and records every request,
    so a test can drive the real agent loop and check exactly what the model was shown."""

    name = "scripted"

    def __init__(self, steps: list[Step]) -> None:
        self._steps = list(steps)
        self.requests: list[list[dict[str, Any]]] = []

    async def respond(
        self,
        *,
        system: str,
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        on_text: OnText,
    ) -> LLMTurn:
        self.requests.append(json.loads(json.dumps(messages)))
        if not self._steps:
            raise AssertionError("The scripted model ran out of steps")
        turn = self._steps.pop(0)(messages)
        for block in turn.content:
            if block["type"] == "text":
                await on_text(block["text"])
        return turn

    def tool_results(self, request_index: int) -> list[dict[str, Any]]:
        """The tool_result blocks the model was given in one request."""
        last = self.requests[request_index][-1]
        return [
            b for b in last["content"] if isinstance(b, dict) and b.get("type") == "tool_result"
        ]
