"""RabbitMQ consumer — списание из ФБС зоны"""

import json
import logging
from typing import List

import aio_pika
from pydantic import ValidationError

from app.shared.config import settings
from app.infrastructure.database.connection import get_db_pool
from app.handlers.write_off_fbs_handler import handle_write_off_fbs
from app.core.schemas.write_off_fbs import WriteOffAccordingToFBS

logger = logging.getLogger(__name__)


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

            try:
                raw: list = json.loads(message.body)
            except json.JSONDecodeError as e:
                logger.error(f"Битый JSON в сообщении: {e}")
                # ACK — битые данные не имеет смысла повторять
                await message.ack()
                continue

            try:
                items: List[WriteOffAccordingToFBS] = [
                    WriteOffAccordingToFBS(**i) for i in raw
                ]
            except ValidationError as e:
                logger.error(f"Ошибка валидации схемы: {e}")
                # NACK без requeue — невалидные данные уйдут в DLQ если настроен
                await message.nack(requeue=False)
                continue

            first_product = items[0].product_id if items else "—"
            logger.info(
                f"Получено сообщение: {len(items)} позиций, "
                f"первый product_id={first_product}"
            )

            try:
                pool = await get_db_pool()
                shipment_id = await handle_write_off_fbs(items, pool, raw_message=raw)
                logger.info(f"Сообщение обработано | shipment_id={shipment_id}")
            except Exception as e:
                # Даже если handler упал — данные могли сохраниться в БД,
                # retry будет по таблице fbs_shipment_items, а не по очереди
                logger.error(f"Ошибка обработки сообщения: {e}", exc_info=True)

            # ACK всегда после успешного парсинга — дальше работаем по данным в БД
            await message.ack()
