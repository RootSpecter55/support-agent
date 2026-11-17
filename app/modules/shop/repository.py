from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.shop.models import Customer, Order, Refund, Return


class ShopRepository:
    """Data access for the fake shop. Every order lookup takes the customer id, so a query can
    only ever see that customer's orders."""

    async def customer(self, session: AsyncSession, customer_id: UUID) -> Customer | None:
        return await session.get(Customer, customer_id)

    async def order_for_customer(
        self, session: AsyncSession, customer_id: UUID, number: str, *, lock: bool = False
    ) -> Order | None:
        query = select(Order).where(Order.number == number, Order.customer_id == customer_id)
        return await session.scalar(query.with_for_update() if lock else query)

    async def orders_for_customer(
        self, session: AsyncSession, customer_id: UUID, status: str | None, limit: int
    ) -> list[Order]:
        query = select(Order).where(Order.customer_id == customer_id)
        if status:
            query = query.where(Order.status == status)
        rows = await session.scalars(query.order_by(Order.placed_at.desc()).limit(limit))
        return list(rows)

    async def refunded_cents(self, session: AsyncSession, order_id: UUID) -> int:
        total = await session.scalar(
            select(func.coalesce(func.sum(Refund.amount_cents), 0)).where(
                Refund.order_id == order_id
            )
        )
        return int(total or 0)

    async def return_for_order(self, session: AsyncSession, order_id: UUID) -> Return | None:
        return await session.scalar(select(Return).where(Return.order_id == order_id))

    async def add(self, session: AsyncSession, row: Return | Refund) -> None:
        session.add(row)
        await session.flush()
