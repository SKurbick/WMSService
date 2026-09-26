"""Репозиторий raw inbox и controlled B2 apply импорта КИЗ."""

import json
from contextlib import asynccontextmanager
from typing import Any

from asyncpg import Connection, Pool, Record

from app.infrastructure.database.queries import kiz_import as queries


def _decode_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _normalize_row(row: Record | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for field in ("raw_payload", "headers", "business_result"):
        result[field] = _decode_json(result.get(field))
    return result


class KizImportRepository:
    def __init__(self, pool: Pool):
        self.pool = pool

    @asynccontextmanager
    async def transaction(self):
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                yield connection

    async def create_message(self, **values: Any) -> Record:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                return await connection.fetchrow(
                    queries.INSERT_MESSAGE,
                    values["raw_body"],
                    json.dumps(values["raw_payload"], ensure_ascii=False)
                    if values["raw_payload"] is not None
                    else None,
                    values["parse_status"],
                    values["parse_error"],
                    values["order_guid"],
                    values["supply_number"],
                    values["wild_group_count"],
                    values["mark_code_count"],
                    values["exchange_name"],
                    values["routing_key"],
                    values["rabbit_message_id"],
                    values["correlation_id"],
                    json.dumps(values["headers"], ensure_ascii=False),
                )

    async def list_messages(self, limit: int, offset: int) -> list[dict[str, Any]]:
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(queries.LIST_MESSAGES, limit, offset)
        return [_normalize_row(row) for row in rows]

    async def get_message(self, message_id: int) -> dict[str, Any] | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(queries.GET_MESSAGE, message_id)
        return _normalize_row(row)

    async def lock_message(self, connection: Connection, message_id: int) -> dict[str, Any] | None:
        return _normalize_row(await connection.fetchrow(queries.LOCK_MESSAGE, message_id))

    async def mark_applied(
        self, connection: Connection, message_id: int, result: dict[str, Any]
    ) -> None:
        await connection.fetchval(
            queries.MARK_MESSAGE_APPLIED,
            message_id,
            json.dumps(result, ensure_ascii=False, default=str),
        )

    async def mark_rejected(
        self, connection: Connection, message_id: int, error_code: str, message: str
    ) -> None:
        await connection.execute(
            queries.MARK_MESSAGE_REJECTED,
            message_id,
            error_code,
            message,
        )

    async def lock_receipt_location(self, connection: Connection, location_code: str):
        return await connection.fetchrow(queries.LOCK_RECEIPT_LOCATION, location_code)

    async def lock_products(self, connection: Connection, product_ids: list[str]):
        return await connection.fetch(queries.LOCK_PRODUCTS, product_ids)

    async def lock_receipt_items(self, connection: Connection, order_guid: str):
        return await connection.fetch(queries.LOCK_RECEIPT_ITEMS, order_guid)

    async def lock_loose_inventory(
        self, connection: Connection, location_id: int, product_ids: list[str]
    ):
        return await connection.fetch(
            queries.LOCK_LOOSE_INVENTORY,
            location_id,
            product_ids,
        )

    async def lock_existing_codes(self, connection: Connection, codes: list[str]):
        if not codes:
            return []
        return await connection.fetch(queries.LOCK_EXISTING_CODES, codes)

    async def lock_active_receipt_kiz(
        self, connection: Connection, order_guid: str, product_ids: list[str]
    ):
        return await connection.fetch(
            queries.LOCK_ACTIVE_RECEIPT_KIZ,
            order_guid,
            product_ids,
        )

    async def lock_active_loose_kiz(
        self, connection: Connection, location_id: int, product_ids: list[str]
    ):
        return await connection.fetch(
            queries.LOCK_ACTIVE_LOOSE_KIZ,
            location_id,
            product_ids,
        )

    async def insert_kiz(
        self,
        connection: Connection,
        *,
        kiz_code: str,
        product_id: str,
        location_id: int,
        order_guid: str,
        author: str,
        metadata: dict[str, Any],
    ):
        return await connection.fetchrow(
            queries.INSERT_KIZ,
            kiz_code,
            product_id,
            location_id,
            order_guid,
            author,
            json.dumps(metadata, ensure_ascii=False),
        )

    async def lock_kiz_by_code(self, connection: Connection, kiz_code: str):
        return await connection.fetchrow(queries.LOCK_KIZ_BY_CODE, kiz_code)

    async def insert_assigned_event(
        self,
        connection: Connection,
        *,
        kiz_id: int,
        product_id: str,
        location_id: int,
        author: str,
        metadata: dict[str, Any],
    ) -> int:
        return await connection.fetchval(
            queries.INSERT_ASSIGNED_EVENT,
            kiz_id,
            product_id,
            location_id,
            author,
            json.dumps(metadata, ensure_ascii=False),
        )

    async def link_message_kiz(
        self, connection: Connection, message_id: int, kiz_id: int, was_created: bool
    ) -> None:
        await connection.execute(
            queries.INSERT_MESSAGE_KIZ_LINK,
            message_id,
            kiz_id,
            was_created,
        )

    async def count_active_receipt_kiz(
        self, connection: Connection, order_guid: str, product_ids: list[str]
    ) -> dict[str, int]:
        rows = await connection.fetch(
            queries.COUNT_ACTIVE_RECEIPT_KIZ,
            order_guid,
            product_ids,
        )
        return {row["product_id"]: row["quantity"] for row in rows}

    async def count_active_loose_kiz(
        self, connection: Connection, location_id: int, product_ids: list[str]
    ) -> dict[str, int]:
        rows = await connection.fetch(
            queries.COUNT_ACTIVE_LOOSE_KIZ,
            location_id,
            product_ids,
        )
        return {row["product_id"]: row["quantity"] for row in rows}

    async def get_integrity_issues(self) -> tuple[list[Record], list[Record]]:
        async with self.pool.acquire() as connection:
            async with connection.transaction(readonly=True):
                capacity = await connection.fetch(queries.GET_RECEIPT_CAPACITY_VIOLATIONS)
                orphans = await connection.fetch(queries.GET_ORPHAN_RECEIPT_KIZ)
        return capacity, orphans
