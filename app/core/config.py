from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every setting comes from environment variables and is validated at startup."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = Field(pattern=r"^postgresql\+asyncpg://")
    client_api_key: str = Field(min_length=32)
    staff_api_key: str = Field(min_length=32)

    # LLM
    llm_provider: Literal["fake", "anthropic", "bedrock"] = "fake"
    anthropic_model: str = "claude-opus-5-5"
    bedrock_model: str = "anthropic.claude-opus-5-5"
    aws_region: str | None = None
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_max_tokens: int = Field(default=16_000, gt=0)

    # Agent
    max_tool_rounds: int = Field(default=6, ge=1, le=50)
    conversation_token_budget: int = Field(default=200_000, gt=0)
    refund_approval_threshold_cents: int = Field(default=5000, ge=0)
    price_input_usd_per_mtok: float = Field(default=0, ge=0)
    price_output_usd_per_mtok: float = Field(default=0, ge=0)

    @field_validator("aws_region", mode="before")
    @classmethod
    def empty_means_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def check_provider_settings(self) -> Self:
        if self.llm_provider == "bedrock" and not self.aws_region:
            raise ValueError("LLM_PROVIDER=bedrock needs AWS_REGION")
        if self.client_api_key == self.staff_api_key:
            raise ValueError("CLIENT_API_KEY and STAFF_API_KEY must differ")
        return self


@lru_cache
def get_settings() -> Settings:
    """Singleton: settings are parsed and validated once, then shared."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
