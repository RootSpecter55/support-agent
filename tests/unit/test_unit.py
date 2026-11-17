import json
from decimal import Decimal
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from app.core.config import Settings
from app.modules.conversations.agent import Pricing, tool_result
from app.modules.conversations.models import Conversation, ConversationStatus
from app.modules.conversations.state import ALLOWED_TRANSITIONS, InvalidTransitionError, transition
from app.modules.tools.shop_tools import GetOrder, IssueRefund, ListCustomerOrders
from app.providers.llm.fake import RuleBasedLLM
from app.providers.llm.scripted import ScriptedLLM, call, say
from scripts.start_with_secrets import SecretsError, load_secrets


class TestStateMachine:
    def test_allows_exactly_the_listed_transitions(self) -> None:
        allowed = {
            (a.value, b.value) for a, targets in ALLOWED_TRANSITIONS.items() for b in targets
        }
        assert allowed == {
            ("active", "responding"),
            ("responding", "active"),
            ("responding", "awaiting_approval"),
            ("responding", "escalated"),
            ("awaiting_approval", "responding"),
        }

    def test_refuses_anything_else(self) -> None:
        conversation = Conversation(status=ConversationStatus.ESCALATED)
        with pytest.raises(InvalidTransitionError, match="from escalated to responding"):
            transition(conversation, ConversationStatus.RESPONDING)
        conversation.status = ConversationStatus.ACTIVE
        with pytest.raises(InvalidTransitionError):
            transition(conversation, ConversationStatus.AWAITING_APPROVAL)


class TestToolInputs:
    def test_refuse_extra_fields_so_no_customer_can_be_injected(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ListCustomerOrders.Input.model_validate({"customer_id": "someone-else"})
        with pytest.raises(ValidationError, match="String should match pattern"):
            GetOrder.Input.model_validate({"order_number": "'; DROP TABLE orders; --"})
        with pytest.raises(ValidationError, match="greater than 0"):
            IssueRefund.Input.model_validate(
                {"order_number": "ORD-1001", "amount_cents": 0, "reason": "x y"}
            )

    def test_definitions_are_api_shaped_and_have_no_customer_field(self) -> None:
        definition = GetOrder(MagicMock()).definition()
        assert definition["name"] == "get_order"
        assert definition["input_schema"]["required"] == ["order_number"]
        assert definition["input_schema"]["additionalProperties"] is False
        assert "customer" not in json.dumps(definition["input_schema"]).lower()


class TestCost:
    def test_uses_the_configured_prices_per_million_tokens(self) -> None:
        pricing = Pricing(input_usd_per_mtok=4, output_usd_per_mtok=20)
        assert pricing.cost(1_000_000, 0) == Decimal("4.000000")
        assert pricing.cost(1500, 300) == Decimal("0.012000")
        assert Pricing(0, 0).cost(10_000, 10_000) == Decimal(0)

    def test_tool_result_blocks(self) -> None:
        assert tool_result("t1", {"ok": True}, False) == {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": '{"ok": true}',
        }
        assert tool_result("t1", {"error": "x"}, True)["is_error"] is True


class TestRuleBasedFake:
    @pytest.mark.parametrize(
        ("message", "tool", "tool_input"),
        [
            ("Where is ORD-1002?", "get_order", {"order_number": "ORD-1002"}),
            (
                "Please refund $12.50 on ord-1001",
                "issue_refund",
                {"order_number": "ORD-1001", "amount_cents": 1250},
            ),
            ("I want to return ORD-1001", "create_return", {"order_number": "ORD-1001"}),
            ("show my orders", "list_customer_orders", {}),
            ("Can I speak to a human?", "escalate_to_human", {}),
            (
                "how long does shipping take",
                "search_help_articles",
                {"query": "how long does shipping take"},
            ),
        ],
    )
    async def test_picks_a_tool_from_keywords(
        self, message: str, tool: str, tool_input: dict[str, Any]
    ) -> None:
        texts: list[str] = []

        async def on_text(text: str) -> None:
            texts.append(text)

        turn = await RuleBasedLLM().respond(
            system="",
            tools=[],
            messages=[{"role": "user", "content": [{"type": "text", "text": message}]}],
            on_text=on_text,
        )
        tool_use = turn.content[-1]
        assert (turn.stop_reason, tool_use["name"]) == ("tool_use", tool)
        assert tool_input.items() <= tool_use["input"].items()


class TestScripted:
    async def test_plays_steps_in_order_and_records_requests(self) -> None:
        llm = ScriptedLLM([call(("get_order", {"order_number": "ORD-1"})), say("done")])

        async def ignore(_: str) -> None:
            return None

        first = await llm.respond(
            system="", tools=[], messages=[{"role": "user", "content": "hi"}], on_text=ignore
        )
        second = await llm.respond(system="", tools=[], messages=[], on_text=ignore)
        assert first.stop_reason == "tool_use" and second.content == [
            {"type": "text", "text": "done"}
        ]
        assert llm.requests[0] == [{"role": "user", "content": "hi"}]
        with pytest.raises(AssertionError, match="ran out of steps"):
            await llm.respond(system="", tools=[], messages=[], on_text=ignore)


class TestSettings:
    BASE: ClassVar[dict[str, Any]] = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "client_api_key": "c" * 32,
        "staff_api_key": "s" * 32,
    }

    def test_defaults_to_the_fake_model_and_the_current_claude_model_id(self) -> None:
        settings = Settings(**self.BASE)
        assert (settings.llm_provider, settings.anthropic_model) == ("fake", "claude-opus-5-5")
        # Prices default to 0: set them from the provider's price list, never guessed here.
        assert Settings.model_fields["price_input_usd_per_mtok"].default == 0

    def test_rejects_unsafe_or_incomplete_settings(self) -> None:
        with pytest.raises(ValidationError, match="must differ"):
            Settings(**{**self.BASE, "staff_api_key": "c" * 32})
        with pytest.raises(ValidationError, match="LLM_PROVIDER=bedrock needs AWS_REGION"):
            Settings(**self.BASE, llm_provider="bedrock", aws_region="")


def _client_returning(secret: Any) -> MagicMock:
    client = MagicMock()
    client.get_secret_value.return_value = {"SecretString": secret}
    return client


class TestStartWithSecrets:
    def test_copies_every_key_of_the_aws_secret(self) -> None:
        env = {"SECRETS_SOURCE": "aws", "SECRET_ID": "app/prod"}
        secret = {
            "DATABASE_URL": "d",
            "CLIENT_API_KEY": "c",
            "STAFF_API_KEY": "s",
            "ANTHROPIC_API_KEY": "k",
        }
        load_secrets(env, _client_returning(json.dumps(secret)))
        assert env["ANTHROPIC_API_KEY"] == "k"

    def test_fails_fast(self) -> None:
        with pytest.raises(SecretsError, match="SECRET_ID is required"):
            load_secrets({"SECRETS_SOURCE": "aws"}, _client_returning("{}"))
        failing = MagicMock()
        failing.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetSecretValue"
        )
        with pytest.raises(SecretsError, match='Could not read secret "x"'):
            load_secrets({"SECRETS_SOURCE": "aws", "SECRET_ID": "x"}, failing)
        with pytest.raises(
            SecretsError, match="missing required keys: CLIENT_API_KEY, STAFF_API_KEY"
        ):
            load_secrets(
                {"SECRETS_SOURCE": "aws", "SECRET_ID": "x"},
                _client_returning(json.dumps({"DATABASE_URL": "d"})),
            )
