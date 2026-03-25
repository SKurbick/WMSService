"""RabbitMQ consumer — списание из ФБС зоны"""

import asyncio
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
            async with message.process(requeue=True):
                try:
                    raw: list = json.loads(message.body)
                except json.JSONDecodeError as e:
                    logger.error(f"Битый JSON в сообщении: {e}")
                    # ACK — битые данные не имеет смысла повторять
                    return

                try:
                    items: List[WriteOffAccordingToFBS] = [
                        WriteOffAccordingToFBS(**i) for i in raw
                    ]
                except ValidationError as e:
                    logger.error(f"Ошибка валидации схемы: {e}")
                    # NACK — невалидные данные уйдут в dead-letter queue
                    raise

                logger.info(f"Получено сообщение: {len(items)} позиций")
                pool = await get_db_pool()
                await handle_write_off_fbs(items, pool)
                logger.info(f"Сообщение обработано: {len(items)} позиций")
