import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.modules.tools.base import Tool, ToolContext, ToolError, ToolOutcome


@dataclass(frozen=True)
class Execution:
    outcome: ToolOutcome
    is_error: bool
    duration_ms: int


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def definitions(self) -> list[dict[str, Any]]:
        return [tool.definition() for tool in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    async def execute(self, name: str, raw_input: Any, ctx: ToolContext) -> Execution:
        """Validates the model's input with the tool's Pydantic model, then runs the tool.
        Bad input, unknown tools and expected failures come back as error results for the
        model; they never raise."""
        started = time.perf_counter()

        def done(outcome: ToolOutcome, is_error: bool) -> Execution:
            return Execution(outcome, is_error, round((time.perf_counter() - started) * 1000))

        tool = self._tools.get(name)
        if tool is None:
            return done(ToolOutcome({"error": f"Unknown tool {name}"}), True)
        try:
            args = tool.Input.model_validate(raw_input)
        except ValidationError as exc:
            problems = [
                f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors()
            ]
            return done(ToolOutcome({"error": "Invalid input", "problems": problems}), True)
        try:
            return done(await tool.run(ctx, args), False)
        except ToolError as exc:
            return done(ToolOutcome({"error": str(exc)}), True)
