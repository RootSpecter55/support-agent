import json
from decimal import Decimal

from app.providers.llm.base import LLMTurn
from app.providers.llm.scripted import call, say
from tests.integration.conftest import CLIENT, Factory, rows, say_to, start

ORDER = ("get_order", {"order_number": "ORD-1001"})


async def test_streams_text_tool_events_and_done_in_order(agent: Factory) -> None:
    client, _ = await agent([call(ORDER, text="Checking."), say("It was delivered.")])
    conversation = await start(client)
    events = await say_to(client, conversation, "Where is ORD-1001?")
    assert [name for name, _ in events] == ["text", "tool", "text", "done"]
    assert events[1][1] == {"name": "get_order", "isError": False, "pendingApproval": False}


async def test_invalid_tool_input_is_returned_to_the_model_as_an_error(agent: Factory) -> None:
    client, llm = await agent(
        [
            call(("get_order", {"order_number": "1001"})),
            call(("issue_refund", {"order_number": "ORD-1001", "amount_cents": -5, "reason": "x"})),
            say("Please give me the full order number."),
        ]
    )
    conversation = await start(client)
    await say_to(client, conversation, "refund order 1001")
    first, second = llm.tool_results(1)[0], llm.tool_results(2)[0]
    assert first["is_error"] and "order_number: String should match pattern" in first["content"]
    problems = json.loads(second["content"])["problems"]
    assert problems == [
        "amount_cents: Input should be greater than 0",
        "reason: String should have at least 3 characters",
    ]


async def test_stops_after_the_maximum_tool_rounds_and_answers_every_tool_call(
    agent: Factory,
) -> None:
    # MAX_TOOL_ROUNDS is 3 in tests; the model would keep calling tools forever.
    client, llm = await agent(
        [call(ORDER), call(ORDER), call(ORDER), call(ORDER), say("Hi again.")]
    )
    conversation = await start(client)
    events = await say_to(client, conversation, "loop please")
    assert dict(events)["stopped"] == {"reason": "tool_round_limit"}
    assert len(llm.requests) == 4

    history = (
        await client.get(f"/conversations/{conversation}/messages?pageSize=50", headers=CLIENT)
    ).json()
    last = history["data"][-1]["content"][0]
    assert last["type"] == "tool_result" and last["is_error"] is True
    # The history is still valid: the next message goes through normally.
    assert dict(await say_to(client, conversation, "hello"))["done"] == {"status": "active"}


async def test_stops_when_the_token_budget_is_spent(agent: Factory) -> None:
    client, llm = await agent([call(ORDER), say("never reached")], conversation_token_budget=100)
    conversation = await start(client)
    events = await say_to(client, conversation, "Where is ORD-1001?")
    assert dict(events)["stopped"] == {"reason": "token_budget_exhausted"}
    assert len(llm.requests) == 1
    refused = await client.post(
        f"/conversations/{conversation}/messages", headers=CLIENT, json={"content": "more"}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "BUDGET_EXHAUSTED"


async def test_records_tokens_and_cost_from_the_configured_prices(agent: Factory) -> None:
    client, _ = await agent([call(ORDER), say("Delivered.")])
    conversation = await start(client)
    await say_to(client, conversation, "Where is ORD-1001?")
    data = (await client.get(f"/conversations/{conversation}", headers=CLIENT)).json()["data"]
    # Two calls of 100 input tokens; 20 + 3 output tokens. $4 / $20 per million in tests.
    assert (data["inputTokens"], data["outputTokens"]) == (200, 23)
    expected = (Decimal(200) * 4 + Decimal(23) * 20) / 1_000_000
    assert Decimal(data["costUsd"]) == expected
    calls = await rows("SELECT sum(cost_usd) AS total FROM llm_calls")
    assert calls[0]["total"] == expected


async def test_the_history_is_sent_back_exactly_as_the_model_returned_it(agent: Factory) -> None:
    thinking = {"type": "thinking", "thinking": "", "signature": "sig-abc"}

    def think_then_call(messages: list) -> LLMTurn:
        tool_use = {
            "type": "tool_use",
            "id": "toolu_x",
            "name": "get_order",
            "input": {"order_number": "ORD-1001"},
        }
        return LLMTurn([thinking, tool_use], "tool_use", 100, 20, "scripted")

    client, llm = await agent([think_then_call, say("Delivered.")])
    conversation = await start(client)
    await say_to(client, conversation, "Where is ORD-1001?")
    assert llm.requests[1][-2]["content"][0] == thinking
    shown = (await client.get(f"/conversations/{conversation}/messages", headers=CLIENT)).json()
    assert all(b["type"] != "thinking" for m in shown["data"] for b in m["content"])


async def test_a_refusal_ends_the_turn(agent: Factory) -> None:
    client, _ = await agent([say("", stop_reason="refusal")])
    conversation = await start(client)
    events = await say_to(client, conversation, "something the model declines")
    assert dict(events)["stopped"] == {"reason": "refusal"}
    assert dict(events)["done"] == {"status": "active"}


async def test_escalation_hands_the_conversation_to_a_person(agent: Factory) -> None:
    client, _ = await agent(
        [call(("escalate_to_human", {"reason": "wants a person"})), say("A colleague will reply.")]
    )
    conversation = await start(client)
    events = await say_to(client, conversation, "I want to talk to a person")
    assert dict(events)["done"] == {"status": "escalated"}
    after = await client.post(
        f"/conversations/{conversation}/messages", headers=CLIENT, json={"content": "hello?"}
    )
    assert after.status_code == 409


async def test_one_turn_at_a_time(agent: Factory) -> None:
    client, _ = await agent([])
    conversation = await start(client)
    await rows("UPDATE conversations SET status = 'responding' WHERE id = :id", id=conversation)
    busy = await client.post(
        f"/conversations/{conversation}/messages", headers=CLIENT, json={"content": "hi"}
    )
    assert busy.status_code == 409


async def test_a_failing_model_call_leaves_the_conversation_usable(agent: Factory) -> None:
    def boom(_: list) -> LLMTurn:
        raise ConnectionError("API unreachable")

    client, _ = await agent([boom, say("Back again.")])
    conversation = await start(client)
    events = await say_to(client, conversation, "hi")
    assert dict(events)["error"]["code"] == "AGENT_ERROR"
    assert dict(events)["done"] == {"status": "active"}
    assert dict(await say_to(client, conversation, "hi again"))["done"] == {"status": "active"}
