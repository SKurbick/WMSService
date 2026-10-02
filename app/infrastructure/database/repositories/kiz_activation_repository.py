"""Repository for explicit KIZ activation."""

import json
from contextlib import asynccontextmanager
from typing import Any

from asyncpg import Pool

from app.infrastructure.database.queries import kiz_activation as queries


class KizActivationRepository:
    def __init__(self, pool: Pool):
        self.pool = pool

    @asynccontextmanager
    async def transaction(self):
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                yield connection

    async def lock_location(self, connection, location_code: str):
        return await connection.fetchrow(queries.LOCK_LOCATION, location_code)

    async def lock_operation_by_key(self, connection, idempotency_key: str):
        return await connection.fetchrow(
            queries.LOCK_OPERATION_BY_KEY,
            idempotency_key,
        )

    async def create_or_lock_operation(
        self,
        connection,
        *,
        idempotency_key: str,
        request_hash: str,
        user_name: str,
    ):
        operation = await connection.fetchrow(
            queries.CREATE_OPERATION,
            idempotency_key,
            request_hash,
            user_name,
        )
        if operation is not None:
            return operation, True
        operation = await self.lock_operation_by_key(connection, idempotency_key)
        return operation, False

    async def lock_kiz_by_codes(self, connection, kiz_codes: list[str]):
        return await connection.fetch(queries.LOCK_KIZ_BY_CODES, kiz_codes)

    async def lock_product_scope(self, connection, location_id: int, product_id: str):
        await connection.fetchval(
            queries.LOCK_PRODUCT_SCOPE,
            location_id,
            product_id,
        )

    async def lock_loose_inventory(self, connection, product_id: str, location_id: int):
        return await connection.fetchrow(
            queries.LOCK_LOOSE_INVENTORY,
            product_id,
            location_id,
        )

    async def count_active_loose_kiz(self, connection, product_id: str, location_id: int) -> int:
        return await connection.fetchval(
            queries.COUNT_ACTIVE_LOOSE_KIZ,
            product_id,
            location_id,
        )

    async def create_operation_item(
        self,
        connection,
        *,
        operation_id: int,
        kiz: Any,
    ) -> int:
        return await connection.fetchval(
            queries.CREATE_OPERATION_ITEM,
            operation_id,
            kiz["kiz_code"],
        )

    async def authorize_activation(
        self,
        connection,
        *,
        operation_id: int,
        kiz: Any,
        location_id: int,
    ) -> None:
        await connection.execute(
            queries.AUTHORIZE_ACTIVATION,
            operation_id,
            kiz["kiz_id"],
            kiz["product_id"],
            location_id,
        )

    async def activate_kiz(self, connection, kiz_id: int, location_id: int):
        return await connection.fetchrow(
            queries.ACTIVATE_KIZ,
            kiz_id,
            location_id,
        )

    async def create_activated_event(
        self,
        connection,
        *,
        kiz: Any,
        location_id: int,
        user_name: str,
        metadata: dict[str, Any],
    ) -> int:
        return await connection.fetchval(
            queries.CREATE_ACTIVATED_EVENT,
            kiz["kiz_id"],
            kiz["product_id"],
            location_id,
            user_name,
            json.dumps(metadata, ensure_ascii=False),
        )

    async def complete_operation(
        self, connection, operation_id: int, result: dict[str, Any]
    ) -> None:
        await connection.fetchval(
            queries.COMPLETE_OPERATION,
            operation_id,
            json.dumps(result, ensure_ascii=False),
        )
