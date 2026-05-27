"""Бизнес-логика мягких резервов товара"""

import logging
from decimal import Decimal
from typing import Any, Optional

from pydantic import ValidationError

from app.core.exceptions import LocationNotFoundError
from app.core.schemas.stock_reservation import (
    ProductAvailabilityResponse,
    ProductAvailabilityTotalsResponse,
    RabbitReservationMessage,
    StockReservationEventResponse,
    StockReservationOrderResponse,
)
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.infrastructure.database.repositories.stock_reservation_repository import (
    StockReservationRepository,
)

logger = logging.getLogger(__name__)


class StockReservationService:
    """Обрабатывает мягкие резервы без изменения физических остатков."""

    RESERVED_STATUSES = {"new", "processing", "fictitious"}
    RELEASE_STATUSES = {"shipped", "burned"}
    SOURCE_TYPE = "fbs"
    RESERVED_QTY = Decimal("1")

    def __init__(
        self,
        repository: StockReservationRepository,
        location_repository: Optional[LocationRepository] = None,
    ):
        self.repository = repository
        self.location_repo = location_repository

    async def process_rabbitmq_message(self, raw_message: Any) -> dict[str, int]:
        """
        Обрабатывает одно RabbitMQ-сообщение с резервами.

        Бизнес-ошибки пишутся в audit и не прерывают обработку. Ошибки БД
        пробрасываются наружу, чтобы caller сделал NACK/retry.
        """
        stats = {
            "processed": 0,
            "released": 0,
            "unknown_status": 0,
            "product_not_found": 0,
            "invalid_payload": 0,
        }

        async with self.repository.pool.acquire() as conn:
            async with conn.transaction():
                if not isinstance(raw_message, list):
                    await self.repository.insert_reservation_event(
                        conn,
                        source_type=self.SOURCE_TYPE,
                        processing_result="invalid_payload",
                        raw_payload=raw_message,
                        error_message="Reservation message must be a JSON array",
                    )
                    stats["invalid_payload"] += 1
                    return stats

                for item in raw_message:
                    try:
                        message_item = RabbitReservationMessage.model_validate(item)
                    except ValidationError as e:
                        await self.repository.insert_reservation_event(
                            conn,
                            source_type=self.SOURCE_TYPE,
                            processing_result="invalid_payload",
                            raw_payload=item,
                            product_id=item.get("wild") if isinstance(item, dict) else None,
                            error_message=str(e),
                        )
                        stats["invalid_payload"] += 1
                        continue

                    for order in message_item.orders:
                        result = await self.process_reservation_order(
                            conn=conn,
                            product_id=message_item.wild,
                            external_order_id=order.order_id,
                            external_status=order.status,
                            external_created_at=order.created_at,
                            raw_payload={
                                "wild": message_item.wild,
                                "order": order.model_dump(mode="json"),
                            },
                        )
                        stats[result] += 1

        return stats

    async def process_reservation_order(
        self,
        conn,
        product_id: str,
        external_order_id: int,
        external_status: str,
        external_created_at,
        raw_payload: dict,
    ) -> str:
        """Обрабатывает один заказ резерва внутри внешней транзакции."""
        reserved_qty = self.RESERVED_QTY

        if external_status in self.RESERVED_STATUSES:
            processing_result = "processed"
            is_reserved = True
        elif external_status in self.RELEASE_STATUSES:
            processing_result = "released"
            is_reserved = False
        else:
            await self.repository.insert_reservation_event(
                conn,
                source_type=self.SOURCE_TYPE,
                product_id=product_id,
                external_order_id=external_order_id,
                external_status=external_status,
                reserved_qty=reserved_qty,
                external_created_at=external_created_at,
                processing_result="unknown_status",
                raw_payload=raw_payload,
            )
            return "unknown_status"

        if not await self.repository.product_exists(conn, product_id):
            await self.repository.insert_reservation_event(
                conn,
                source_type=self.SOURCE_TYPE,
                product_id=product_id,
                external_order_id=external_order_id,
                external_status=external_status,
                reserved_qty=reserved_qty,
                external_created_at=external_created_at,
                processing_result="product_not_found",
                raw_payload=raw_payload,
            )
            return "product_not_found"

        await self.repository.upsert_reservation_order(
            conn,
            source_type=self.SOURCE_TYPE,
            product_id=product_id,
            external_order_id=external_order_id,
            external_status=external_status,
            is_reserved=is_reserved,
            reserved_qty=reserved_qty,
            external_created_at=external_created_at,
            raw_payload=raw_payload,
        )
        await self.repository.insert_reservation_event(
            conn,
            source_type=self.SOURCE_TYPE,
            product_id=product_id,
            external_order_id=external_order_id,
            external_status=external_status,
            reserved_qty=reserved_qty,
            external_created_at=external_created_at,
            processing_result=processing_result,
            raw_payload=raw_payload,
        )
        return processing_result

    async def get_product_availability(self, product_id: str) -> ProductAvailabilityResponse:
        row = await self.repository.get_product_availability(product_id)
        return ProductAvailabilityResponse.model_validate(dict(row))

    async def list_product_availability(
        self,
        product_id: Optional[str] = None,
        only_shortage: Optional[bool] = None,
        only_reserved: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProductAvailabilityResponse]:
        rows = await self.repository.list_product_availability(
            product_id=product_id,
            only_shortage=only_shortage,
            only_reserved=only_reserved,
            limit=limit,
            offset=offset,
        )
        return [ProductAvailabilityResponse.model_validate(dict(row)) for row in rows]

    async def get_availability_totals(self) -> ProductAvailabilityTotalsResponse:
        row = await self.repository.get_availability_totals()
        return ProductAvailabilityTotalsResponse.model_validate(dict(row))

    async def get_location_subtree_availability(
        self,
        location_id: int,
    ) -> list[ProductAvailabilityResponse]:
        if self.location_repo is not None:
            location = await self.location_repo.get_by_id(location_id)
            if not location:
                raise LocationNotFoundError(f"Локация с ID {location_id} не найдена")

        rows = await self.repository.get_location_subtree_availability(location_id)
        return [ProductAvailabilityResponse.model_validate(dict(row)) for row in rows]

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
    ) -> list[StockReservationOrderResponse]:
        rows = await self.repository.list_reservations(
            product_id=product_id,
            external_order_id=external_order_id,
            is_reserved=is_reserved,
            external_status=external_status,
            source_type=source_type,
            older_than_hours=older_than_hours,
            limit=limit,
            offset=offset,
        )
        return [StockReservationOrderResponse.model_validate(dict(row)) for row in rows]

    async def list_reservation_events(
        self,
        product_id: Optional[str] = None,
        external_order_id: Optional[int] = None,
        external_status: Optional[str] = None,
        processing_result: Optional[str] = None,
        source_type: Optional[str] = None,
        date_from=None,
        date_to=None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[StockReservationEventResponse]:
        rows = await self.repository.list_reservation_events(
            product_id=product_id,
            external_order_id=external_order_id,
            external_status=external_status,
            processing_result=processing_result,
            source_type=source_type,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
        return [StockReservationEventResponse.model_validate(dict(row)) for row in rows]
