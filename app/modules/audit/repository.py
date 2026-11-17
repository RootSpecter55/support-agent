from app.core.base_repository import BaseRepository
from app.modules.audit.models import ToolCall


class ToolCallRepository(BaseRepository[ToolCall]):
    model = ToolCall
