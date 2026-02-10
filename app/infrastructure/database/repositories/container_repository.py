"""Репозиторий для работы с контейнерами"""

import json
from typing import List, Optional
from asyncpg import Pool, Record
from app.infrastructure.database.queries import containers as queries


class ContainerRepository:
    """Репозиторий для работы с таблицей wms.containers"""

    def __init__(self, pool: Pool):
        self.pool = pool

    def _parse_record(self, record: Record) -> dict:
        """Конвертирует asyncpg.Record в dict с парсингом JSON полей"""
        data = dict(record)
        for field in ("contents", "metadata"):
            if isinstance(data.get(field), str):
                data[field] = json.loads(data[field])
        return data


    async def register(
        self, qr_code: str, container_type: str, location_code: str, contents: list
    ) -> Record:
        """
        Зарегистрировать контейнер

        Вызывает PostgreSQL функцию wms.register_container()
        которая создаёт контейнер, содержимое и события receive в movements
        """
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                queries.REGISTER_CONTAINER,
                qr_code,
                container_type,
                location_code,
                json.dumps(contents),
            )
            return result

    async def get_by_qr_code(self, qr_code: str) -> Optional[Record]:
        """Получить контейнер по QR-коду с содержимым"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(queries.GET_CONTAINER_BY_QR, qr_code)
            return self._parse_record(result) if result else None

    async def get_by_id(self, container_id: int) -> Optional[Record]:
        """Получить контейнер по ID"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(queries.GET_CONTAINER_BY_ID, container_id)
            return result

    async def update_location(self, container_id: int, location_code: str) -> Optional[Record]:
        """Обновить локацию контейнера"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                queries.UPDATE_CONTAINER_LOCATION, container_id, location_code
            )
            return result

    async def unpack(self, qr_code: str, product_id: str, quantity: int) -> Optional[Record]:
        """
        Вскрыть контейнер и извлечь товар

        Вызывает PostgreSQL функцию wms.unpack_from_container()
        """
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                queries.UNPACK_FROM_CONTAINER, qr_code, product_id, quantity
            )
            return result

    async def update_status(self, container_id: int, status: str) -> Optional[Record]:
        """Обновить статус контейнера"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                queries.UPDATE_CONTAINER_STATUS, container_id, status
            )
            return result

    async def get_history(self, qr_code: str) -> List[Record]:
        """Получить историю контейнера"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(queries.GET_CONTAINER_HISTORY, qr_code)
            return results

    async def get_containers_in_location(
        self,
        location_id: int,
        status: Optional[str] = None,
        container_type: Optional[str] = None,
    ) -> List[Record]:
        """Получить контейнеры в локации"""
        async with self.pool.acquire() as conn:
            results = await conn.fetch(
                queries.GET_CONTAINERS_IN_LOCATION, location_id, status, container_type
            )
            return results

    async def exists(self, qr_code: str) -> bool:
        """Проверить существование контейнера по QR-коду"""
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(queries.CHECK_CONTAINER_EXISTS, qr_code)
            return result is not None
