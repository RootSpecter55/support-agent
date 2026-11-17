from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from app.core.pagination import PageParams, page_params
from app.modules.help_articles.controller import HelpArticlesController


def help_articles_router(
    controller: HelpArticlesController, require_client: Callable[..., Any]
) -> APIRouter:
    router = APIRouter(
        prefix="/help-articles", tags=["help articles"], dependencies=[Depends(require_client)]
    )

    @router.get("", summary="Help articles; with ?q=, full-text search ranked by relevance")
    async def list_articles(
        page: Annotated[PageParams, Depends(page_params)],
        q: Annotated[str | None, Query(min_length=2, max_length=200)] = None,
    ) -> dict[str, Any]:
        return await controller.list(q, page)

    @router.get("/{slug}", summary="One help article")
    async def get(slug: Annotated[str, Path(pattern=r"^[a-z0-9-]{1,100}$")]) -> dict[str, Any]:
        return await controller.get(slug)

    return router
