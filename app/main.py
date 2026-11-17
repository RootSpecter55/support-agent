from fastapi import FastAPI

from app.container import Container, build_container
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.pagination import data_envelope
from app.modules.actions.routes import actions_router
from app.modules.audit.routes import audit_router
from app.modules.conversations.routes import conversations_router
from app.modules.help_articles.routes import help_articles_router
from app.modules.tools.routes import tools_router


def create_app(container: Container | None = None) -> FastAPI:
    configure_logging()
    c = container or build_container(get_settings())

    app = FastAPI(
        title="Support agent",
        version="1.0.0",
        description="A customer-support agent on Claude tool use, over a fake shop.",
    )
    register_error_handlers(app)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, object]:
        return data_envelope({"status": "ok"})

    app.include_router(conversations_router(c.conversations, c.require_client))
    app.include_router(help_articles_router(c.help_articles, c.require_client))
    app.include_router(tools_router(c.tools, c.require_client))
    app.include_router(actions_router(c.actions, c.require_staff))
    app.include_router(audit_router(c.audit, c.require_staff))
    return app
