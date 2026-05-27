"""RabbitMQ consumer — списание из ФБС зоны"""

import json
import logging
from typing import List

import aio_pika
from pydantic import ValidationError

from app.shared.config import settings
from app.infrastructure.database.connection import get_db_pool
from app.infrastructure.database.repositories.fbs_shipment_repository import FbsShipmentRepository
from app.infrastructure.database.repositories.stock_reservation_repository import StockReservationRepository
from app.core.services.stock_reservation_service import StockReservationService
from app.handlers.write_off_fbs_handler import handle_write_off_fbs
from app.core.schemas.write_off_fbs import WriteOffAccordingToFBS

logger = logging.getLogger(__name__)

fbs_shipment_repo = FbsShipmentRepository()


async def start_consumer() -> None:
    connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)

    async with connection:
        channel = await connection.channel()

        # passive=True — подключиться к уже существующей очереди, не пересоздавать
        queue = await channel.declare_queue(
            settings.RABBITMQ_QUEUE,
            passive=True,
        )

        logger.info(f"Consumer запущен, слушаю очередь: {settings.RABBITMQ_QUEUE}")

        async for message in queue:
            # Управляем ACK/NACK вручную — не используем message.process()

            # === Шаг 1: JSON парсинг ===
            try:
                raw: list = json.loads(message.body)
            except json.JSONDecodeError as e:
                logger.error(f"Битый JSON в сообщении: {e}")
                # ACK — битые данные не имеет смысла повторять
                await message.ack()
                continue

            logger.debug(f"Raw message: {len(raw)} элементов, первый: {raw[0] if raw else 'пустой'}")

            pool = await get_db_pool()

            # === Шаг 2: Сохранить raw JSON в БД ДО валидации ===
            try:
                async with pool.acquire() as conn:
                    shipment_id = await fbs_shipment_repo.create_shipment(
                        conn,
                        raw_message=raw,
                        total_items=len(raw) if isinstance(raw, list) else 0,
                    )
                logger.info(f"Shipment сохранён: shipment_id={shipment_id}, элементов={len(raw)}")
            except Exception as e:
                logger.error(f"Не удалось сохранить shipment в БД: {e}", exc_info=True)
                # Не смогли записать даже raw — NACK с requeue, попробуем позже
                await message.nack(requeue=True)
                continue

            # === Шаг 3: ACK — данные в БД, сообщение можно удалить из очереди ===
            await message.ack()

            # === Шаг 4: Валидация Pydantic ===
            try:
                items: List[WriteOffAccordingToFBS] = [
                    WriteOffAccordingToFBS(**i) for i in raw
                ]
            except ValidationError as e:
                logger.error(f"Ошибка валидации схемы (shipment_id={shipment_id}): {e}")
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE wms.fbs_shipments
                        SET status = 'validation_failed', error_message = $1
                        WHERE shipment_id = $2
                        """,
                        str(e),
                        shipment_id,
                    )
                continue

            # === Шаг 5: Обработка — создание items и списание ===
            try:
                await handle_write_off_fbs(
                    items, pool, raw_message=raw, shipment_id=shipment_id
                )
                logger.info(f"Сообщение обработано, shipment_id={shipment_id}")
            except Exception as e:
                logger.error(f"Ошибка обработки (shipment_id={shipment_id}): {e}", exc_info=True)


async def start_stock_reservation_consumer() -> None:
    """RabbitMQ consumer для мягких резервов товара.

    Слушает отдельную очередь резервов и не обрабатывает FBS write-off сообщения.
    При пустой очереди consumer просто ожидает новые сообщения.
    """
    connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)

    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue(
            settings.STOCK_RESERVATION_QUEUE,
            passive=True,
        )

        logger.info(
            f"Stock reservation consumer запущен, слушаю очередь: "
            f"{settings.STOCK_RESERVATION_QUEUE}"
        )

        async for message in queue:
            try:
                raw = json.loads(message.body)
            except json.JSONDecodeError as e:
                logger.error(f"Битый JSON в сообщении резервов: {e}")
                await message.ack()
                continue

            pool = await get_db_pool()
            reservation_service = StockReservationService(StockReservationRepository(pool))

            try:
                stats = await reservation_service.process_rabbitmq_message(raw)
                logger.info(f"Сообщение резервов обработано: {stats}")
                await message.ack()
            except Exception as e:
                logger.error(
                    "Ошибка обработки stock reservation message. "
                    "Проверьте доступность таблиц/view резервов в БД. "
                    f"queue={settings.STOCK_RESERVATION_QUEUE} error={e}",
                    exc_info=True,
                )
                await message.nack(requeue=True)
