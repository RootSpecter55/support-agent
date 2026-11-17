from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_repository import BaseRepository
from app.modules.help_articles.models import HelpArticle


class HelpArticleRepository(BaseRepository[HelpArticle]):
    model = HelpArticle

    async def search(
        self, session: AsyncSession, query: str, offset: int, limit: int
    ) -> tuple[list[HelpArticle], int]:
        """Postgres full-text search: websearch syntax ("quotes", -exclude, or), ranked by
        ts_rank, where title words weigh more than body words."""
        tsquery = func.websearch_to_tsquery("english", query)
        match = HelpArticle.search.op("@@")(tsquery)
        rows = await session.scalars(
            select(HelpArticle)
            .where(match)
            .order_by(func.ts_rank(HelpArticle.search, tsquery).desc(), HelpArticle.title)
            .offset(offset)
            .limit(limit)
        )
        total = await session.scalar(select(func.count()).select_from(HelpArticle).where(match))
        return list(rows), total or 0

    async def by_slug(self, session: AsyncSession, slug: str) -> HelpArticle | None:
        return await session.scalar(select(HelpArticle).where(HelpArticle.slug == slug))
