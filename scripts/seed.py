"""Fills the fake shop (customers, products, orders, help articles). Safe to run repeatedly.

    python -m scripts.seed
"""

import asyncio

from dotenv import load_dotenv

from app.core.db import Database, get_engine
from app.modules.shop.seed import seed


async def main() -> None:
    load_dotenv()  # never overrides variables that are already set
    async with Database(get_engine()).transaction() as session:
        await seed(session)
    await get_engine().dispose()
    print("Shop seeded (customers ada@, grace@ and linus@example.com)")


if __name__ == "__main__":
    asyncio.run(main())
