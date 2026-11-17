from typing import Any

from app.core.pagination import PageParams, data_envelope, page_envelope
from app.modules.help_articles.service import HelpArticlesService


class HelpArticlesController:
    def __init__(self, service: HelpArticlesService) -> None:
        self._service = service

    async def list(self, query: str | None, page: PageParams) -> dict[str, Any]:
        items, total = await self._service.list(query, page)
        return page_envelope([a.to_json() for a in items], total, page)

    async def get(self, slug: str) -> dict[str, Any]:
        return data_envelope((await self._service.get(slug)).to_json())
