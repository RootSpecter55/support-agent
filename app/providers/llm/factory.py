from anthropic import AsyncAnthropic, AsyncAnthropicBedrockMantle

from app.core.config import Settings
from app.providers.llm.anthropic_provider import AnthropicProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.fake import RuleBasedLLM


# Factory: picks the model provider from settings; the agent loop sees only LLMProvider.
def create_llm(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(
            AsyncAnthropic(),  # ANTHROPIC_API_KEY or an `ant auth login` profile
            name="anthropic",
            model=settings.anthropic_model,
            max_tokens=settings.llm_max_tokens,
            effort=settings.llm_effort,
            server_side_fallback=True,
        )
    if settings.llm_provider == "bedrock":
        return AnthropicProvider(
            AsyncAnthropicBedrockMantle(aws_region=settings.aws_region),
            name="bedrock",
            model=settings.bedrock_model,
            max_tokens=settings.llm_max_tokens,
            effort=settings.llm_effort,
            server_side_fallback=False,  # not offered on Bedrock: a refusal ends the turn
        )
    return RuleBasedLLM()
