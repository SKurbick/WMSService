"""RabbitMQ consumer — списание из ФБС зоны"""

import asyncio
import json
import logging
from typing import List

import aio_pika
from pydantic import ValidationError

from app.shared.config import settings
from app.infrastructure.database.connection import get_db_pool
from app.infrastructure.database.repositories.fbs_shipment_repository import FbsShipmentRepository
from app.infrastructure.database.repositories.stock_reservation_repository import (
    StockReservationRepository,
)
from app.core.services.stock_reservation_service import StockReservationService
from app.handlers.write_off_fbs_handler import handle_write_off_fbs
from app.core.schemas.write_off_fbs import WriteOffAccordingToFBS
from app.core.enums import FbsShipmentSource

logger = logging.getLogger(__name__)
reservation_logger = logging.getLogger(f"{__name__}.stock_reservation")
reservation_logger.setLevel(logging.INFO)

fbs_shipment_repo = FbsShipmentRepository()


async def consume_fbs_queue(*, queue_name: str, source: FbsShipmentSource) -> None:
    connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)

    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue(queue_name, passive=True)
        logger.info(f"FBS consumer запущен | queue_name={queue_name} | source={source.value}")

        async for message in queue:
            try:
                raw: list = json.loads(message.body)
            except json.JSONDecodeError as e:
                logger.error(
                    f"Битый JSON | queue_name={queue_name} | source={source.value} | error={e}"
                )
                await message.ack()
                continue

            pool = await get_db_pool()
            try:
                async with pool.acquire() as conn:
                    shipment_id = await fbs_shipment_repo.create_shipment(
                        conn,
                        raw_message=raw,
                        total_items=len(raw) if isinstance(raw, list) else 0,
                        source=source.value,
                    )
            except Exception as e:
                logger.error(
                    f"Не удалось сохранить shipment | queue_name={queue_name} | source={source.value} | error={e}",
                    exc_info=True,
                )
                await message.nack(requeue=True)
                continue

            await message.ack()
            try:
                items: List[WriteOffAccordingToFBS] = [WriteOffAccordingToFBS(**i) for i in raw]
            except ValidationError as e:
                logger.error(
                    f"Ошибка валидации | shipment_id={shipment_id} | source={source.value} | error={e}"
                )
                async with pool.acquire() as conn:
                    await fbs_shipment_repo.mark_validation_failed(conn, shipment_id, str(e))
                continue

            try:
                await handle_write_off_fbs(
                    items, pool, raw_message=raw, shipment_id=shipment_id, source=source
                )
            except Exception as e:
                logger.error(
                    f"Ошибка обработки | shipment_id={shipment_id} | source={source.value} | error={e}",
                    exc_info=True,
                )


async def start_consumer() -> None:
    await consume_fbs_queue(queue_name=settings.RABBITMQ_QUEUE, source=FbsShipmentSource.STANDARD)


async def start_external_fbs_consumer() -> None:
    await consume_fbs_queue(
        queue_name=settings.EXTERNAL_FBS_QUEUE, source=FbsShipmentSource.EXTERNAL_DETECTED
    )


async def start_stock_reservation_consumer() -> None:
    """RabbitMQ consumer для мягких резервов товара.

    Слушает отдельную очередь резервов и не обрабатывает FBS write-off сообщения.
    При пустой очереди consumer просто ожидает новые сообщения.
    """
    queue_name = settings.STOCK_RESERVATION_QUEUE
    stage = "connect"
    reservation_logger.info("Stock reservation consumer: подключение | queue=%s", queue_name)

    try:
        connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
        reservation_logger.info(
            "Stock reservation consumer: RabbitMQ подключён | queue=%s", queue_name
        )

        async with connection:
            stage = "open_channel"
            channel = await connection.channel()

            stage = "declare_queue"
            queue = await channel.declare_queue(queue_name, passive=True)
            reservation_logger.info(
                "Stock reservation consumer готов и слушает очередь | queue=%s",
                queue_name,
            )

            stage = "consume"
            async for message in queue:
                await _process_stock_reservation_message(message, queue_name=queue_name)
    except asyncio.CancelledError:
        reservation_logger.info("Stock reservation consumer остановлен | queue=%s", queue_name)
        raise
    except Exception:
        reservation_logger.exception(
            "Stock reservation consumer завершился с ошибкой | queue=%s | stage=%s",
            queue_name,
            stage,
        )
        raise


async def _process_stock_reservation_message(message, *, queue_name: str) -> None:
    message_id = message.message_id or "-"
    routing_key = message.routing_key or "-"
    reservation_logger.info(
        "Stock reservation message получено | queue=%s | message_id=%s | "
        "routing_key=%s | redelivered=%s | body_size=%s",
        queue_name,
        message_id,
        routing_key,
        message.redelivered,
        len(message.body),
    )

    try:
        raw = json.loads(message.body)
    except json.JSONDecodeError as error:
        reservation_logger.error(
            "Битый JSON в сообщении резервов | queue=%s | message_id=%s | error=%s",
            queue_name,
            message_id,
            error,
        )
        await message.ack()
        reservation_logger.info(
            "Stock reservation message ACK | queue=%s | message_id=%s | reason=invalid_json",
            queue_name,
            message_id,
        )
        return

    reservation_logger.info(
        "Stock reservation DB processing начат | queue=%s | message_id=%s",
        queue_name,
        message_id,
    )

    try:
        pool = await get_db_pool()
        reservation_service = StockReservationService(StockReservationRepository(pool))
        stats = await reservation_service.process_rabbitmq_message(raw)
    except Exception as error:
        reservation_logger.error(
            "Stock reservation DB processing завершился ошибкой | queue=%s | "
            "message_id=%s | error=%s",
            queue_name,
            message_id,
            error,
            exc_info=True,
        )
        await message.nack(requeue=True)
        reservation_logger.warning(
            "Stock reservation message NACK/requeue | queue=%s | message_id=%s",
            queue_name,
            message_id,
        )
        return

    reservation_logger.info(
        "Stock reservation DB transaction зафиксирована | queue=%s | "
        "message_id=%s | stats=%s",
        queue_name,
        message_id,
        stats,
    )
    await message.ack()
    reservation_logger.info(
        "Stock reservation message ACK | queue=%s | message_id=%s",
        queue_name,
        message_id,
    )
