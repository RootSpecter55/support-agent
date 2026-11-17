import json

from app.providers.llm.scripted import call, say
from tests.integration.conftest import CLIENT, STAFF, Factory, rows, say_to, start


async def test_help_article_search_is_ranked(agent: Factory) -> None:
    client, _ = await agent([])
    found = (await client.get("/help-articles?q=refund", headers=CLIENT)).json()
    assert found["data"][0]["slug"] == "refund-timing"  # title match outranks body matches
    assert {a["slug"] for a in found["data"]} >= {"refund-timing", "returns-policy"}
    nothing = (await client.get("/help-articles?q=submarine", headers=CLIENT)).json()
    assert nothing["meta"]["total"] == 0
    article = await client.get("/help-articles/warranty", headers=CLIENT)
    assert article.json()["data"]["title"] == "Warranty"
    assert (await client.get("/help-articles/nope", headers=CLIENT)).status_code == 404


async def test_keys_are_separate(agent: Factory) -> None:
    client, _ = await agent([])
    assert (await client.get("/actions", headers=CLIENT)).status_code == 401
    assert (await client.get("/audit/tool-calls", headers=CLIENT)).status_code == 401
    assert (
        await client.post("/conversations", headers=STAFF, json={"customerId": "x"})
    ).status_code == 401
    assert (await client.get("/tools")).status_code == 401


async def test_conversation_and_message_validation(agent: Factory) -> None:
    client, _ = await agent([])
    unknown = await client.post(
        "/conversations",
        headers=CLIENT,
        json={"customerId": "00000000-0000-4000-8000-000000000000"},
    )
    assert unknown.status_code == 404
    conversation = await start(client)
    empty = await client.post(
        f"/conversations/{conversation}/messages", headers=CLIENT, json={"content": ""}
    )
    assert empty.status_code == 400


async def test_lists_the_tools_without_any_customer_field(agent: Factory) -> None:
    client, _ = await agent([])
    tools = (await client.get("/tools", headers=CLIENT)).json()
    assert tools["meta"]["total"] == 6
    for tool in tools["data"]:
        assert "customer" not in json.dumps(tool["input_schema"]).lower()


async def test_returns_follow_the_shop_rules(agent: Factory) -> None:
    client, llm = await agent(
        [
            call(
                ("create_return", {"order_number": "ORD-1003", "reason": "too old"}),
                ("create_return", {"order_number": "ORD-1002", "reason": "not here yet"}),
                ("create_return", {"order_number": "ORD-1001", "reason": "changed my mind"}),
            ),
            call(("create_return", {"order_number": "ORD-1001", "reason": "again"})),
            say("Your return for ORD-1001 is started."),
        ]
    )
    conversation = await start(client)
    await say_to(client, conversation, "return everything")
    old, shipped, ok = (json.loads(r["content"]) for r in llm.tool_results(1))
    assert old == {"error": "ORD-1003 was delivered more than 30 days ago"}
    assert shipped == {"error": "ORD-1002 has not been delivered, so it cannot be returned yet"}
    assert ok["returnStatus"] == "requested"
    assert json.loads(llm.tool_results(2)[0]["content"]) == {
        "error": "A return for ORD-1001 already exists"
    }
    assert len(await rows("SELECT * FROM returns")) == 1


async def test_refunds_cannot_exceed_what_is_left(agent: Factory) -> None:
    client, llm = await agent(
        [
            call(
                (
                    "issue_refund",
                    {"order_number": "ORD-1001", "amount_cents": 4000, "reason": "part"},
                )
            ),
            call(
                (
                    "issue_refund",
                    {"order_number": "ORD-1001", "amount_cents": 4900, "reason": "more"},
                )
            ),
            say("Done."),
        ]
    )
    conversation = await start(client)
    await say_to(client, conversation, "refund")
    assert json.loads(llm.tool_results(1)[0]["content"])["status"] == "refunded"
    # ORD-1001 is $85.00; $40 is gone, so $45 is left.
    assert json.loads(llm.tool_results(2)[0]["content"]) == {
        "error": "At most $45.00 of ORD-1001 can still be refunded"
    }
