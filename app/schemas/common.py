from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar('T')


class PaginationResult(BaseModel, Generic[T]):
	items: list[T]
	total: int
	page: int
	per_page: int
	pages: int
