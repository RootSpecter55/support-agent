"""Test configuration. The environment is set before any app module reads settings."""

import os

os.environ.update(
    {
        "APP_ENV": "test",
        "LOG_LEVEL": "WARNING",
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:5451/support_test"
        ),
        "CLIENT_API_KEY": "test-client-key-that-is-at-least-32-chars",
        "STAFF_API_KEY": "test-staff-key-that-is-at-least-32-chars!",
        "LLM_PROVIDER": "fake",
        "MAX_TOOL_ROUNDS": "3",
        "CONVERSATION_TOKEN_BUDGET": "100000",
        "REFUND_APPROVAL_THRESHOLD_CENTS": "5000",
        "PRICE_INPUT_USD_PER_MTOK": "4",
        "PRICE_OUTPUT_USD_PER_MTOK": "20",
    }
)
