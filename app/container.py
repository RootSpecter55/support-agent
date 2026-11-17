from dataclasses import dataclass
from typing import Any

from app.core.auth import make_key_auth
from app.core.config import Settings
from app.core.db import Database, get_engine
from app.modules.actions.controller import ActionsController
from app.modules.actions.repository import ActionRepository
from app.modules.actions.service import ActionsService
from app.modules.audit.controller import AuditController
from app.modules.audit.repository import ToolCallRepository
from app.modules.audit.service import AuditService
from app.modules.conversations.agent import AgentRunner, Pricing
from app.modules.conversations.controller import ConversationsController
from app.modules.conversations.repository import ConversationRepository
from app.modules.conversations.service import ConversationsService
from app.modules.help_articles.controller import HelpArticlesController
from app.modules.help_articles.repository import HelpArticleRepository
from app.modules.help_articles.service import HelpArticlesService
from app.modules.messages.repository import LlmCallRepository, MessageRepository
from app.modules.shop.repository import ShopRepository
from app.modules.tools.registry import ToolRegistry
from app.modules.tools.routes import ToolsController
from app.modules.tools.shop_tools import (
    CreateReturn,
    EscalateToHuman,
    GetOrder,
    IssueRefund,
    ListCustomerOrders,
    SearchHelpArticles,
)
from app.providers.llm.base import LLMProvider
from app.providers.llm.factory import create_llm


@dataclass
class Container:
    require_client: Any
    require_staff: Any
    conversations: ConversationsController
    actions: ActionsController
    audit: AuditController
    help_articles: HelpArticlesController
    tools: ToolsController
    # Used by the eval script.
    conversations_service: ConversationsService
    agent: AgentRunner


def build_container(settings: Settings, *, llm: LLMProvider | None = None) -> Container:
    """Composition root: wires every repository, service and controller in one place. The
    LLM comes from its factory unless passed in (tests pass a scripted model)."""
    db = Database(get_engine())
    shop, articles = ShopRepository(), HelpArticleRepository()
    conversations, messages = ConversationRepository(), MessageRepository()
    actions = ActionRepository()
    audit = AuditService(db, ToolCallRepository())

    refund_tool = IssueRefund(shop, settings.refund_approval_threshold_cents)
    registry = ToolRegistry(
        [
            GetOrder(shop),
            ListCustomerOrders(shop),
            SearchHelpArticles(articles),
            CreateReturn(shop),
            refund_tool,
            EscalateToHuman(),
        ]
    )
    agent = AgentRunner(
        db,
        llm or create_llm(settings),
        registry,
        conversations,
        messages,
        LlmCallRepository(),
        actions,
        shop,
        audit,
        max_tool_rounds=settings.max_tool_rounds,
        pricing=Pricing(settings.price_input_usd_per_mtok, settings.price_output_usd_per_mtok),
    )
    conversations_service = ConversationsService(
        db, conversations, messages, shop, agent, settings.conversation_token_budget
    )
    return Container(
        require_client=make_key_auth(settings.client_api_key, "client key"),
        require_staff=make_key_auth(settings.staff_api_key, "staff key"),
        conversations=ConversationsController(conversations_service),
        actions=ActionsController(
            ActionsService(db, actions, conversations, messages, audit, refund_tool, agent)
        ),
        audit=AuditController(audit),
        help_articles=HelpArticlesController(HelpArticlesService(db, articles)),
        tools=ToolsController(registry),
        conversations_service=conversations_service,
        agent=agent,
    )
