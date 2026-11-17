import itertools
import json
import re
from typing import Any

from app.providers.llm.base import LLMTurn, OnText

ORDER = re.compile(r"\bORD-\d{4,}\b", re.IGNORECASE)
AMOUNT = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")


class RuleBasedLLM:
    """A stand-in model for running the app without an API key: picks a tool from keywords in
    the customer's message, then sums up the tool result. Deterministic and crude on purpose;
    it exists to exercise the same agent loop, not to be a good agent."""

    name = "fake"

    def __init__(self) -> None:
        self._ids = itertools.count(1)

    async def respond(
        self,
        *,
        system: str,
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        on_text: OnText,
    ) -> LLMTurn:
        last = messages[-1]["content"]
        results = [b for b in last if isinstance(b, dict) and b.get("type") == "tool_result"]
        if results:
            text = self._summarise(results)
            for chunk in re.findall(r"\S+\s*", text):  # stream word by word
                await on_text(chunk)
            return LLMTurn(
                [{"type": "text", "text": text}], "end_turn", 400, len(text) // 4, "fake"
            )

        user_text = " ".join(
            b["text"] for b in last if isinstance(b, dict) and b.get("type") == "text"
        )
        name, tool_input = self._choose(user_text)
        call = {
            "type": "tool_use",
            "id": f"toolu_fake_{next(self._ids)}",
            "name": name,
            "input": tool_input,
        }
        await on_text("Let me check that. ")
        return LLMTurn(
            [{"type": "text", "text": "Let me check that. "}, call], "tool_use", 400, 40, "fake"
        )

    @staticmethod
    def _choose(text: str) -> tuple[str, dict[str, Any]]:
        lower = text.lower()
        order = ORDER.search(text)
        number = order.group(0).upper() if order else None
        if any(word in lower for word in ("human", "person", "agent", "manager")):
            return "escalate_to_human", {"reason": text[:200]}
        if number and "refund" in lower:
            amount = AMOUNT.search(text)
            cents = round(float(amount.group(1)) * 100) if amount else 100
            return "issue_refund", {
                "order_number": number,
                "amount_cents": cents,
                "reason": text[:200],
            }
        if number and "return" in lower:
            return "create_return", {"order_number": number, "reason": text[:200]}
        if number:
            return "get_order", {"order_number": number}
        if "my orders" in lower or "orders" in lower:
            return "list_customer_orders", {}
        return "search_help_articles", {"query": text[:200] or "help"}

    @staticmethod
    def _summarise(results: list[dict[str, Any]]) -> str:
        parts = []
        for result in results:
            content = result.get("content")
            data = json.loads(content) if isinstance(content, str) else content
            if result.get("is_error"):
                parts.append(f"Sorry, I couldn't do that: {data.get('error', data)}.")
            elif "articles" in data:
                titles = ", ".join(a["title"] for a in data["articles"]) or "nothing relevant"
                parts.append(f"These help articles may answer it: {titles}.")
            elif "orders" in data:
                listing = "; ".join(f"{o['orderNumber']} ({o['status']})" for o in data["orders"])
                parts.append(f"Your recent orders: {listing or 'none'}.")
            elif data.get("status") == "refunded":
                parts.append(f"Done: {data['refunded']} refunded on {data['orderNumber']}.")
            elif data.get("status") == "rejected":
                parts.append("A colleague reviewed the refund and could not approve it.")
            elif "returnStatus" in data and "label" in data:
                parts.append(
                    f"I started a return for {data['orderNumber']}; the label is on its way."
                )
            elif data.get("escalated"):
                parts.append(f"I've passed this to a colleague. {data['nextStep']}")
            elif "orderNumber" in data:
                parts.append(f"{data['orderNumber']} is {data['status']} (total {data['total']}).")
            else:
                parts.append("Done.")
        return " ".join(parts)
