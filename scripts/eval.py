"""Runs the agent on scenario files and reports pass/fail per scenario.

    uv run python -m scripts.eval                     # the configured LLM_PROVIDER (default: fake)
    LLM_PROVIDER=anthropic uv run python -m scripts.eval --yes

Each scenario (eval/scenarios/*.json) is a customer's first message, plus the tools the agent
must call and the tools it must not call:

    {"name": "...", "customerEmail": "ada@example.com", "message": "...",
     "mustCall": ["get_order"], "mustNotCall": ["issue_refund"]}

Every scenario runs in a new conversation against the seeded shop (it may create refunds and
returns there), and the tools called are read from the audit log. With LLM_PROVIDER=fake this
only shows the harness working; the scenarios are meant for a real model. A real model costs
money, so the script refuses to run one without --yes. Exit code 1 if any scenario fails.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import select

from app.container import build_container
from app.core.config import get_settings
from app.core.db import Database, get_engine
from app.core.pagination import PageParams
from app.modules.shop.models import Customer

SCENARIOS = Path(__file__).resolve().parent.parent / "eval" / "scenarios"


async def run_scenario(container: Any, scenario: dict[str, Any]) -> tuple[bool, list[str], str]:
    """Returns (passed, tools called, explanation)."""
    async with Database(get_engine()).transaction() as session:
        customer_id = await session.scalar(
            select(Customer.id).where(Customer.email == scenario["customerEmail"])
        )
    if customer_id is None:
        return False, [], f"unknown customer {scenario['customerEmail']} (is the shop seeded?)"

    conversations = container.conversations_service
    conversation = await conversations.create(customer_id)
    await conversations.start_turn(conversation.id, scenario["message"])
    async for _ in conversations.stream_turn(conversation.id):
        pass

    everything = PageParams(page=1, page_size=100)
    calls, _ = await container.audit_service.list(conversation.id, everything)
    called = [c.tool_name for c in calls]
    missing = [t for t in scenario["mustCall"] if t not in called]
    forbidden = [t for t in scenario["mustNotCall"] if t in called]
    problems = [f"did not call {', '.join(missing)}"] if missing else []
    problems += [f"called {', '.join(forbidden)}"] if forbidden else []
    return not problems, called, "; ".join(problems)


async def main() -> int:
    load_dotenv()
    settings = get_settings()
    if settings.llm_provider != "fake" and "--yes" not in sys.argv:
        print(f"LLM_PROVIDER={settings.llm_provider} calls a paid API. Re-run with --yes.")
        return 2

    container = build_container(settings)
    files = sorted(SCENARIOS.glob("*.json"))
    failures = 0
    print(f"provider: {settings.llm_provider}, scenarios: {len(files)}\n")
    for path in files:
        scenario = json.loads(path.read_text())
        passed, called, why = await run_scenario(container, scenario)
        failures += not passed
        verdict = "PASS" if passed else "FAIL"
        print(f"{verdict}  {scenario['name']:<24} tools: {', '.join(called) or '-'}")
        if why:
            print(f"      {why}")
    print(f"\n{len(files) - failures}/{len(files)} passed")
    await get_engine().dispose()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
