"""The fake shop's data: three customers, a handful of orders, and the help centre."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.help_articles.models import HelpArticle
from app.modules.shop.models import Customer, Order, OrderItem, Product

PRODUCTS = [
    ("MUG-01", "Stoneware mug", 1800),
    ("KETTLE-01", "Electric kettle", 4900),
    ("LAMP-01", "Desk lamp", 4500),
    ("CHAIR-01", "Office chair", 22500),
    ("NOTE-01", "Notebook (3 pack)", 1200),
    ("HEAD-01", "Wireless headphones", 13900),
]

# customer email, name, [(order number, status, days since placed, days since delivered, items)]
CUSTOMERS = [
    (
        "ada@example.com",
        "Ada Lovelace",
        [
            ("ORD-1001", "delivered", 8, 5, [("KETTLE-01", 1), ("MUG-01", 2)]),
            ("ORD-1002", "shipped", 2, None, [("LAMP-01", 1)]),
            ("ORD-1003", "delivered", 70, 65, [("NOTE-01", 3)]),
        ],
    ),
    (
        "grace@example.com",
        "Grace Hopper",
        [
            ("ORD-2001", "delivered", 6, 3, [("HEAD-01", 1)]),
            ("ORD-2002", "processing", 1, None, [("MUG-01", 4)]),
        ],
    ),
    (
        "linus@example.com",
        "Linus Torvalds",
        [("ORD-3001", "delivered", 14, 10, [("CHAIR-01", 2)])],
    ),
]

ARTICLES = [
    (
        "returns-policy",
        "Returns policy",
        "You can return most items within 30 days of delivery for a full refund. Items must be "
        "unused and in their original packaging. Start a return from your order page or ask "
        "support; we email a prepaid return label.",
    ),
    (
        "refund-timing",
        "When will I get my refund?",
        "Refunds go back to the original payment method. Once we confirm the refund it takes 3 "
        "to 5 business days to appear on your card statement.",
    ),
    (
        "shipping-times",
        "Shipping times",
        "Standard shipping takes 3 to 5 business days. Express shipping takes 1 to 2 business "
        "days. Orders placed before 2pm ship the same day.",
    ),
    (
        "track-order",
        "How do I track my order?",
        "When your order ships we email a tracking link. You can also see the order status and "
        "tracking from your account page.",
    ),
    (
        "change-address",
        "Changing the delivery address",
        "You can change the delivery address while the order is still processing. Once it has "
        "shipped the address can no longer be changed; contact the courier instead.",
    ),
    (
        "damaged-item",
        "My item arrived damaged",
        "We are sorry! Send a photo of the damage to support within 7 days of delivery and we "
        "will send a replacement or refund the item, whichever you prefer.",
    ),
    (
        "warranty",
        "Warranty",
        "Electrical items such as kettles, lamps and headphones carry a two-year warranty "
        "against manufacturing faults. Keep your order number as proof of purchase.",
    ),
    (
        "payment-methods",
        "Payment methods",
        "We accept credit and debit cards and PayPal. Your card is charged when the order ships.",
    ),
]


async def seed(session: AsyncSession, now: datetime | None = None) -> None:
    """Idempotent: does nothing if the shop already has customers."""
    if await session.scalar(select(Customer).limit(1)) is not None:
        return
    now = now or datetime.now(UTC)
    products = {
        sku: Product(sku=sku, name=name, price_cents=price) for sku, name, price in PRODUCTS
    }
    session.add_all(products.values())
    for email, name, orders in CUSTOMERS:
        customer = Customer(email=email, name=name)
        session.add(customer)
        await session.flush()
        for number, status, placed, delivered, items in orders:
            lines = [
                OrderItem(
                    product_id=products[sku].id,
                    quantity=quantity,
                    unit_price_cents=products[sku].price_cents,
                )
                for sku, quantity in items
            ]
            session.add(
                Order(
                    number=number,
                    customer_id=customer.id,
                    status=status,
                    total_cents=sum(line.quantity * line.unit_price_cents for line in lines),
                    placed_at=now - timedelta(days=placed),
                    delivered_at=now - timedelta(days=delivered) if delivered is not None else None,
                    items=lines,
                )
            )
    session.add_all(HelpArticle(slug=s, title=t, body=b) for s, t, b in ARTICLES)
    await session.flush()
