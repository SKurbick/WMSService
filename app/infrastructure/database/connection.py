"""Подключение к базе данных через asyncpg"""

import asyncpg
from asyncpg import Pool
from app.shared.config import settings

_pool: Pool | None = None


async def get_db_pool() -> Pool:
    """
    Получить connection pool для asyncpg
    
    Создаёт глобальный pool при первом вызове.
    Используется в FastAPI dependencies.
    """
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            database=settings.DB_NAME,
            min_size=5,
            max_size=20,
            command_timeout=60,
            server_settings={
                "search_path": "wms,public"
            },
        )
    return _pool


async def close_db_pool():
    """Закрыть connection pool"""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
