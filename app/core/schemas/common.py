"""Общие Pydantic схемы"""

from pydantic import BaseModel, Field
from typing import Optional


class PaginationParams(BaseModel):
    """Параметры пагинации"""

    limit: int = Field(default=100, ge=1, le=1000, description="Количество записей")
    offset: int = Field(default=0, ge=0, description="Смещение")


class ErrorResponse(BaseModel):
    """Схема ошибки"""

    detail: str = Field(..., description="Описание ошибки")
    error_code: str = Field(..., description="Код ошибки")


class SuccessResponse(BaseModel):
    """Схема успешного ответа"""

    success: bool = Field(default=True, description="Статус операции")
    message: Optional[str] = Field(None, description="Сообщение")
