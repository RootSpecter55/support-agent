"""The six tools. Each takes the customer from ToolContext; none has a customer field."""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import Field

from app.modules.help_articles.repository import HelpArticleRepository
from app.modules.shop.models import Order, Refund, Return
from app.modules.shop.repository import ShopRepository
from app.modules.tools.base import (
    NeedsApproval,
    Tool,
    ToolContext,
    ToolError,
    ToolInput,
    ToolOutcome,
)

ORDER_NUMBER = Field(pattern=r"^ORD-\d{4,}$", description="The order number, e.g. ORD-1001")
RETURN_WINDOW = timedelta(days=30)


def _not_found(number: str) -> ToolError:
    # The same answer whether the order belongs to someone else or does not exist at all.
    return ToolError(f"No order {number} on this customer's account")


def _money(cents: int) -> str:
    return f"${cents / 100:.2f}"


async def _order_view(shop: ShopRepository, ctx: ToolContext, order: Order) -> dict[str, Any]:
    refunded = await shop.refunded_cents(ctx.session, order.id)
    returned = await shop.return_for_order(ctx.session, order.id)
    return {
        "orderNumber": order.number,
        "status": order.status,
        "total": _money(order.total_cents),
        "totalCents": order.total_cents,
        "refundedCents": refunded,
        "placedAt": order.placed_at.date().isoformat(),
        "deliveredAt": order.delivered_at.date().isoformat() if order.delivered_at else None,
        "returnStatus": returned.status if returned else None,
        "items": [
            {
                "product": item.product.name,
                "quantity": item.quantity,
                "unitPrice": _money(item.unit_price_cents),
            }
            for item in order.items
        ],
    }


class GetOrder(Tool):
    name = "get_order"
    description = "Look up one of the current customer's orders by its order number."

    class Input(ToolInput):
        order_number: str = ORDER_NUMBER

    def __init__(self, shop: ShopRepository) -> None:
        self._shop = shop

    async def run(self, ctx: ToolContext, args: Input) -> ToolOutcome:
        order = await self._shop.order_for_customer(ctx.session, ctx.customer_id, args.order_number)
        if order is None:
            raise _not_found(args.order_number)
        return ToolOutcome(await _order_view(self._shop, ctx, order))


class ListCustomerOrders(Tool):
    name = "list_customer_orders"
    description = "List the current customer's most recent orders, optionally by status."

    class Input(ToolInput):
        status: Literal["processing", "shipped", "delivered", "cancelled"] | None = None
        limit: int = Field(default=10, ge=1, le=20)

    def __init__(self, shop: ShopRepository) -> None:
        self._shop = shop

    async def run(self, ctx: ToolContext, args: Input) -> ToolOutcome:
        orders = await self._shop.orders_for_customer(
            ctx.session, ctx.customer_id, args.status, args.limit
        )
        return ToolOutcome(
            {
                "orders": [
                    {
                        "orderNumber": o.number,
                        "status": o.status,
                        "total": _money(o.total_cents),
                        "placedAt": o.placed_at.date().isoformat(),
                    }
                    for o in orders
                ]
            }
        )


class SearchHelpArticles(Tool):
    name = "search_help_articles"
    description = "Search the help centre (returns, refunds, shipping, warranty, ...)."

    class Input(ToolInput):
        query: str = Field(min_length=2, max_length=200)

    def __init__(self, articles: HelpArticleRepository) -> None:
        self._articles = articles

    async def run(self, ctx: ToolContext, args: Input) -> ToolOutcome:
        rows, _ = await self._articles.search(ctx.session, args.query, offset=0, limit=3)
        return ToolOutcome(
            {"articles": [{"slug": a.slug, "title": a.title, "body": a.body} for a in rows]}
        )


class CreateReturn(Tool):
    name = "create_return"
    description = (
        "Start a return for a delivered order of the current customer, within 30 days of "
        "delivery. The customer gets a prepaid return label by email."
    )

    class Input(ToolInput):
        order_number: str = ORDER_NUMBER
        reason: str = Field(min_length=3, max_length=500)

    def __init__(self, shop: ShopRepository) -> None:
        self._shop = shop

    async def run(self, ctx: ToolContext, args: Input) -> ToolOutcome:
        order = await self._shop.order_for_customer(
            ctx.session, ctx.customer_id, args.order_number, lock=True
        )
        if order is None:
            raise _not_found(args.order_number)
        if order.status != "delivered" or order.delivered_at is None:
            raise ToolError(f"{order.number} has not been delivered, so it cannot be returned yet")
        if datetime.now(UTC) - order.delivered_at > RETURN_WINDOW:
            raise ToolError(f"{order.number} was delivered more than 30 days ago")
        if await self._shop.return_for_order(ctx.session, order.id):
            raise ToolError(f"A return for {order.number} already exists")
        await self._shop.add(ctx.session, Return(order_id=order.id, reason=args.reason))
        return ToolOutcome(
            {"orderNumber": order.number, "returnStatus": "requested", "label": "sent by email"}
        )


class IssueRefund(Tool):
    name = "issue_refund"
    description = (
        "Refund part or all of one of the current customer's orders to the original payment "
        "method. Larger refunds are sent to a person for approval first."
    )

    class Input(ToolInput):
        order_number: str = ORDER_NUMBER
        amount_cents: int = Field(gt=0, description="Amount to refund, in cents")
        reason: str = Field(min_length=3, max_length=500)

    def __init__(self, shop: ShopRepository, approval_threshold_cents: int) -> None:
        self._shop = shop
        self._threshold = approval_threshold_cents

    async def _refundable_order(self, ctx: ToolContext, args: Input) -> Order:
        order = await self._shop.order_for_customer(
            ctx.session, ctx.customer_id, args.order_number, lock=True
        )
        if order is None:
            raise _not_found(args.order_number)
        remaining = order.total_cents - await self._shop.refunded_cents(ctx.session, order.id)
        if args.amount_cents > remaining:
            raise ToolError(f"At most {_money(remaining)} of {order.number} can still be refunded")
        return order

    async def run(self, ctx: ToolContext, args: Input) -> ToolOutcome:
        order = await self._refundable_order(ctx, args)
        if args.amount_cents > self._threshold:
            return ToolOutcome(
                approval=NeedsApproval(
                    kind="refund",
                    summary={
                        "orderNumber": order.number,
                        "amount": _money(args.amount_cents),
                        "reason": args.reason,
                    },
                )
            )
        return ToolOutcome(await self.perform(ctx, args, approved_by=None))

    async def perform(
        self, ctx: ToolContext, args: Input, approved_by: str | None
    ) -> dict[str, Any]:
        """Makes the refund. Also called after a person approves, re-checking the order then:
        it may have been refunded in the meantime."""
        order = await self._refundable_order(ctx, args)
        await self._shop.add(
            ctx.session,
            Refund(
                order_id=order.id,
                amount_cents=args.amount_cents,
                reason=args.reason,
                approved_by=approved_by,
            ),
        )
        return {
            "orderNumber": order.number,
            "refunded": _money(args.amount_cents),
            "status": "refunded",
            "arrives": "3-5 business days",
        }


class EscalateToHuman(Tool):
    name = "escalate_to_human"
    description = (
        "Hand the conversation to a human support agent, when the customer asks for one or the "
        "problem is outside what the tools can do."
    )

    class Input(ToolInput):
        reason: str = Field(min_length=3, max_length=500)

    async def run(self, ctx: ToolContext, args: Input) -> ToolOutcome:
        return ToolOutcome(
            {"escalated": True, "nextStep": "A support agent will reply within one business day."},
            escalate=True,
        )
