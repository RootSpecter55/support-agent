import json

from app.providers.llm.scripted import call, say
from tests.integration.conftest import CLIENT, STAFF, Factory, rows, say_to, start

LARGE = ("issue_refund", {"order_number": "ORD-3001", "amount_cents": 30000, "reason": "broken"})


async def test_a_small_refund_runs_straight_away(agent: Factory) -> None:
    client, llm = await agent(
        [
            call(
                (
                    "issue_refund",
                    {"order_number": "ORD-1001", "amount_cents": 1000, "reason": "dent"},
                )
            ),
            say("Refunded $10."),
        ]
    )
    conversation = await start(client)
    events = await say_to(client, conversation, "My kettle has a dent, can I get $10 back?")

    assert "approval_required" not in [name for name, _ in events]
    assert json.loads(llm.tool_results(1)[0]["content"])["status"] == "refunded"
    refunds = await rows("SELECT amount_cents, approved_by FROM refunds")
    assert [dict(r) for r in refunds] == [{"amount_cents": 1000, "approved_by": None}]


async def test_a_large_refund_waits_for_a_person_then_resumes_with_the_outcome(
    agent: Factory,
) -> None:
    client, llm = await agent([call(LARGE), say("Approved: $300 is on its way.")])
    conversation = await start(client, "linus@example.com")

    events = await say_to(client, conversation, "One of the two chairs broke, refund $300 please")

    names = [name for name, _ in events]
    assert names[-2:] == ["approval_required", "done"]
    approval = dict(events)["approval_required"]
    assert approval["amount"] == "$300.00"
    assert dict(events)["done"] == {"status": "awaiting_approval"}
    assert await rows("SELECT * FROM refunds") == []  # nothing happened yet

    # The customer has to wait: the conversation is not taking messages.
    busy = await client.post(
        f"/conversations/{conversation}/messages", headers=CLIENT, json={"content": "hello?"}
    )
    assert busy.status_code == 409
    assert busy.json()["error"]["message"] == "A refund is waiting for approval by our team"
    pending = (await client.get("/actions?status=pending", headers=STAFF)).json()
    assert pending["meta"]["total"] == 1

    decided = await client.post(
        f"/actions/{approval['actionId']}/approve", headers=STAFF, json={"approver": "sam"}
    )
    assert decided.status_code == 200, decided.text
    body = decided.json()["data"]
    assert body["action"]["status"] == "approved"
    assert body["reply"] == "Approved: $300 is on its way."
    assert body["conversationStatus"] == "active"

    # The model's next request carries the outcome as the result of its original tool call.
    result = llm.tool_results(1)[0]
    assert result["tool_use_id"] == llm.requests[1][-2]["content"][0]["id"]
    assert json.loads(result["content"])["status"] == "refunded"
    assert [dict(r) for r in await rows("SELECT amount_cents, approved_by FROM refunds")] == [
        {"amount_cents": 30000, "approved_by": "sam"}
    ]
    audit = await rows(
        "SELECT tool_name, approver, action_id FROM tool_calls WHERE approver IS NOT NULL"
    )
    assert [(a["tool_name"], a["approver"]) for a in audit] == [("issue_refund", "sam")]

    again = await client.post(
        f"/actions/{approval['actionId']}/approve", headers=STAFF, json={"approver": "sam"}
    )
    assert again.status_code == 409


async def test_a_rejected_refund_resumes_without_refunding(agent: Factory) -> None:
    client, llm = await agent([call(LARGE), say("Sorry, that was not approved.")])
    conversation = await start(client, "linus@example.com")
    events = await say_to(client, conversation, "Refund $300 please")
    action_id = dict(events)["approval_required"]["actionId"]

    decided = await client.post(
        f"/actions/{action_id}/reject",
        headers=STAFF,
        json={"approver": "sam", "note": "Photos needed"},
    )
    assert decided.json()["data"]["action"]["status"] == "rejected"
    assert json.loads(llm.tool_results(1)[0]["content"]) == {
        "status": "rejected",
        "reason": "Photos needed",
    }
    assert await rows("SELECT * FROM refunds") == []


async def test_other_results_of_the_same_turn_wait_with_the_approval(agent: Factory) -> None:
    client, llm = await agent(
        [call(("get_order", {"order_number": "ORD-3001"}), LARGE), say("Done.")]
    )
    conversation = await start(client, "linus@example.com")
    events = await say_to(client, conversation, "Check ORD-3001 and refund $300")
    action_id = dict(events)["approval_required"]["actionId"]

    await client.post(f"/actions/{action_id}/approve", headers=STAFF, json={"approver": "sam"})

    # Both tool_use blocks are answered together, in one user turn.
    results = llm.tool_results(1)
    assert [json.loads(r["content"]).get("orderNumber") for r in results] == [
        "ORD-3001",
        "ORD-3001",
    ]
    assert "status" in json.loads(results[1]["content"])


async def test_approval_rechecks_the_order(agent: Factory) -> None:
    client, llm = await agent([call(LARGE), say("Something changed.")])
    conversation = await start(client, "linus@example.com")
    events = await say_to(client, conversation, "Refund $300")
    action_id = dict(events)["approval_required"]["actionId"]
    # Meanwhile the order was refunded in full by other means.
    await rows(
        "INSERT INTO refunds (id, order_id, amount_cents, reason) "
        "SELECT gen_random_uuid(), id, total_cents, 'manual' FROM orders "
        "WHERE number = 'ORD-3001' RETURNING id"
    )

    await client.post(f"/actions/{action_id}/approve", headers=STAFF, json={"approver": "sam"})
    result = llm.tool_results(1)[0]
    assert result["is_error"] is True
    assert json.loads(result["content"]) == {
        "error": "At most $0.00 of ORD-3001 can still be refunded"
    }
