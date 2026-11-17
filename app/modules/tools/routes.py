from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.pagination import PageParams, page_envelope, page_params
from app.modules.tools.registry import ToolRegistry


class ToolsController:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def list(self, page: PageParams) -> dict[str, Any]:
        definitions = self._registry.definitions()
        items = definitions[page.offset : page.offset + page.page_size]
        return page_envelope(items, len(definitions), page)


def tools_router(controller: ToolsController, require_client: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/tools", tags=["tools"], dependencies=[Depends(require_client)])

    @router.get("", summary="The tools the agent can use, with their input schemas")
    async def list_tools(page: Annotated[PageParams, Depends(page_params)]) -> dict[str, Any]:
        return controller.list(page)

    return router
