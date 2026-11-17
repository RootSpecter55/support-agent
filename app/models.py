"""Imports every ORM model so Base.metadata is complete (Alembic and tests use this)."""

from app.core.db import Base
from app.modules.actions.models import Action
from app.modules.audit.models import ToolCall
from app.modules.conversations.models import Conversation
from app.modules.help_articles.models import HelpArticle
from app.modules.messages.models import LlmCall, Message
from app.modules.shop.models import Customer, Order, OrderItem, Product, Refund, Return

__all__ = [
    "Action",
    "Base",
    "Conversation",
    "Customer",
    "HelpArticle",
    "LlmCall",
    "Message",
    "Order",
    "OrderItem",
    "Product",
    "Refund",
    "Return",
    "ToolCall",
]
