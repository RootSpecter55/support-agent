import asyncio
import json
import os
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.container import build_container
from app.core.config import get_settings
from app.core.db import Database, get_engine
from app.main import create_app
from app.modules.shop.models import Customer
from app.modules.shop.seed import seed
from app.providers.llm.scripted import ScriptedLLM, Step

CLIENT = {"Authorization": f"Bearer {os.environ['CLIENT_API_KEY']}"}
STAFF = {"Authorization": f"Bearer {os.environ['STAFF_API_KEY']}"}


async def _create_database_if_missing() -> None:
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    base, db_name = url.rsplit("/", 1)
    connection = await asyncpg.connect(f"{base}/postgres")
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await connection.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await connection.close()


@pytest.fixture(scope="session", autouse=True)
async def database() -> AsyncIterator[None]:
    """Creates and migrates the test database, and seeds the shop once per session."""
    await _create_database_if_missing()
    await asyncio.to_thread(
        subprocess.run,
        ["alembic", "upgrade", "head"],
        check=True,
        capture_output=True,
        env=os.environ,
    )
    async with Database(get_engine()).transaction() as session:
        await seed(session)
    yield
    await get_engine().dispose()


@pytest.fixture(autouse=True)
async def clean_state() -> None:
    """Conversations, refunds and returns are per test; the seeded shop is shared."""
    async with get_engine().begin() as connection:
        await connection.execute(text("TRUNCATE conversations, refunds, returns CASCADE"))


Factory = Callable[..., Awaitable[tuple[AsyncClient, ScriptedLLM]]]


@pytest.fixture
async def agent() -> AsyncIterator[Factory]:
    """Builds the real app around a scripted model: agent(steps, **setting_overrides)."""
    clients: list[AsyncClient] = []

    async def factory(steps: list[Step], **overrides: Any) -> tuple[AsyncClient, ScriptedLLM]:
        llm = ScriptedLLM(steps)
        settings = get_settings().model_copy(update=overrides)
        app = create_app(build_container(settings, llm=llm))
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(client)
        return client, llm

    yield factory
    for client in clients:
        await client.aclose()


async def customer_id(email: str) -> str:
    async with Database(get_engine()).transaction() as session:
        return str(await session.scalar(select(Customer.id).where(Customer.email == email)))


async def start(client: AsyncClient, email: str = "ada@example.com") -> str:
    response = await client.post(
        "/conversations", headers=CLIENT, json={"customerId": await customer_id(email)}
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for chunk in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def say_to(
    client: AsyncClient, conversation_id: str, content: str
) -> list[tuple[str, dict[str, Any]]]:
    response = await client.post(
        f"/conversations/{conversation_id}/messages", headers=CLIENT, json={"content": content}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


async def rows(query: str, **params: Any) -> list[Any]:
    """Runs SQL directly (in its own committed transaction) to check or set up state."""
    async with get_engine().begin() as connection:
        result = await connection.execute(text(query), params)
        return list(result.mappings()) if result.returns_rows else []
