from uuid import UUID

from app.core.schemas import ApiModel


class HelpArticleOut(ApiModel):
    id: UUID
    slug: str
    title: str
    body: str
