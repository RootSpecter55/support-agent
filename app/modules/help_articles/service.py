from app.core.db import Database
from app.core.errors import NotFoundError
from app.core.pagination import PageParams
from app.modules.help_articles.models import HelpArticle
from app.modules.help_articles.repository import HelpArticleRepository
from app.modules.help_articles.schemas import HelpArticleOut


class HelpArticlesService:
    def __init__(self, db: Database, articles: HelpArticleRepository) -> None:
        self._db = db
        self._articles = articles

    async def list(self, query: str | None, page: PageParams) -> tuple[list[HelpArticleOut], int]:
        async with self._db.transaction() as session:
            if query:
                rows, total = await self._articles.search(
                    session, query, page.offset, page.page_size
                )
            else:
                rows, total = await self._articles.list(
                    session, order_by=HelpArticle.title, offset=page.offset, limit=page.page_size
                )
        return [HelpArticleOut.model_validate(a) for a in rows], total

    async def get(self, slug: str) -> HelpArticleOut:
        async with self._db.transaction() as session:
            article = await self._articles.by_slug(session, slug)
        if article is None:
            raise NotFoundError("Help article")
        return HelpArticleOut.model_validate(article)
