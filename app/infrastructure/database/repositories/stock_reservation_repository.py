"""Репозиторий для мягких резервов товара"""

import json
from datetime import datetime
from decimal import Decimal
from typing import Optional

from asyncpg import Connection, Pool, Record

from app.infrastructure.database.queries import stock_reservations as queries


class StockReservationRepository:
    """Работа с таблицами резервов и view доступности."""

    def __init__(self, pool: Pool):
        self.pool = pool

    async def product_exists(self, conn: Connection, product_id: str) -> bool:
        return bool(await conn.fetchval(queries.PRODUCT_EXISTS, product_id))

    async def upsert_reservation_order(
        self,
        conn: Connection,
        source_type: str,
        product_id: str,
        external_order_id: int,
        external_status: str,
        is_reserved: bool,
        reserved_qty: Decimal,
        external_created_at: Optional[datetime],
        raw_payload: dict,
    ) -> Record:
        return await conn.fetchrow(
            queries.UPSERT_RESERVATION_ORDER,
            source_type,
            product_id,
            external_order_id,
            external_status,
            is_reserved,
            reserved_qty,
            external_created_at,
            json.dumps(raw_payload, ensure_ascii=False),
        )

    async def insert_reservation_event(
        self,
        conn: Connection,
        source_type: str,
        processing_result: str,
        raw_payload,
        product_id: Optional[str] = None,
        external_order_id: Optional[int] = None,
        external_status: Optional[str] = None,
        reserved_qty: Optional[Decimal] = None,
        external_created_at: Optional[datetime] = None,
        error_message: Optional[str] = None,
    ) -> Record:
        return await conn.fetchrow(
            queries.INSERT_RESERVATION_EVENT,
            source_type,
            product_id,
            external_order_id,
            external_status,
            reserved_qty,
            external_created_at,
            processing_result,
            error_message,
            json.dumps(raw_payload, ensure_ascii=False, default=str),
        )

    async def get_product_availability(self, product_id: str) -> Record:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.GET_PRODUCT_AVAILABILITY, product_id)

    async def list_product_availability(
        self,
        product_id: Optional[str] = None,
        only_shortage: Optional[bool] = None,
        only_reserved: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                queries.LIST_PRODUCT_AVAILABILITY,
                product_id,
                only_shortage,
                only_reserved,
                limit,
                offset,
            )

    async def get_availability_totals(self) -> Record:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(queries.GET_AVAILABILITY_TOTALS)

    async def get_location_subtree_availability(self, location_id: int) -> list[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(queries.GET_LOCATION_SUBTREE_AVAILABILITY, location_id)

    async def list_reservations(
        self,
        product_id: Optional[str] = None,
        external_order_id: Optional[int] = None,
        is_reserved: Optional[bool] = None,
        external_status: Optional[str] = None,
        source_type: Optional[str] = None,
        older_than_hours: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                queries.LIST_RESERVATIONS,
                product_id,
                external_order_id,
                is_reserved,
                external_status,
                source_type,
                older_than_hours,
                limit,
                offset,
            )

    async def list_reservation_events(
        self,
        product_id: Optional[str] = None,
        external_order_id: Optional[int] = None,
        external_status: Optional[str] = None,
        processing_result: Optional[str] = None,
        source_type: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                queries.LIST_RESERVATION_EVENTS,
                product_id,
                external_order_id,
                external_status,
                processing_result,
                source_type,
                date_from,
                date_to,
                limit,
                offset,
            )
