import math
from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel


class PageParams(BaseModel):
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
) -> PageParams:
    """FastAPI dependency: validated ?page=&pageSize= for every list endpoint."""
    return PageParams(page=page, page_size=page_size)


def page_envelope(items: list[Any], total: int, params: PageParams) -> dict[str, Any]:
    """List envelope: {"data": [...], "meta": {page, pageSize, total, totalPages}}."""
    return {
        "data": items,
        "meta": {
            "page": params.page,
            "pageSize": params.page_size,
            "total": total,
            "totalPages": math.ceil(total / params.page_size),
        },
    }


def data_envelope(data: Any) -> dict[str, Any]:
    """Success envelope: {"data": ...}."""
    return {"data": data}
