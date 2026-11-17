from typing import Any

from anthropic import AsyncAnthropic, AsyncAnthropicBedrockMantle

from app.providers.llm.base import LLMTurn, OnText

# On a safety refusal, the API re-runs the request on a fallback model it picks by refusal
# category, inside the same call (server-side; not offered on Bedrock).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_JSON_RETRIES = 2


class AnthropicProvider:
    """Claude through the official SDK, streamed. Used for the Anthropic API (with the
    server-side refusal fallback) and for Amazon Bedrock through the Mantle client.

    Written against the official SDK but not exercised by the tests: it calls a paid API.
    """

    def __init__(
        self,
        client: AsyncAnthropic | AsyncAnthropicBedrockMantle,
        *,
        name: str,
        model: str,
        max_tokens: int,
        effort: str,
        server_side_fallback: bool,
    ) -> None:
        self.name = name
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._effort = effort
        self._fallback = server_side_fallback

    async def respond(
        self,
        *,
        system: str,
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        on_text: OnText,
    ) -> LLMTurn:
        # Tool inputs stream as they are generated. The SDK may then hand back input it could
        # not fully parse; the agent loop validates every input with Pydantic before running it.
        streaming_tools = [{**tool, "eager_input_streaming": True} for tool in tools]
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "tools": streaming_tools,
            "messages": messages,
            # Thinking is always on for this model; effort sets how much it does.
            "output_config": {"effort": self._effort},
        }
        for attempt in range(MAX_JSON_RETRIES + 1):
            try:
                if self._fallback:
                    stream = self._client.beta.messages.stream(
                        **request, betas=[FALLBACK_BETA], fallbacks="default"
                    )
                else:
                    stream = self._client.messages.stream(**request)
                async with stream as events:
                    async for event in events:
                        if event.type == "text":
                            await on_text(event.text)
                    final = await events.get_final_message()
                break
            except ValueError:
                # Tool input JSON the SDK could not parse at all: re-issue the turn. API errors
                # are not ValueError and propagate.
                if attempt == MAX_JSON_RETRIES:
                    raise
        return LLMTurn(
            content=[block.to_dict() for block in final.content],
            stop_reason=final.stop_reason,
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
            model=final.model,
        )
