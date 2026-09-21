import asyncio
import logging

import pytest

from app.main import _log_background_task_completion


@pytest.mark.asyncio
async def test_background_task_failure_is_logged_immediately(caplog):
    async def fail():
        raise RuntimeError("consumer stopped")

    task = asyncio.create_task(fail())
    await asyncio.sleep(0)

    with caplog.at_level(logging.ERROR, logger="app.main"):
        _log_background_task_completion(task, task_name="stock-reservation-consumer")

    assert "Фоновая задача завершилась с ошибкой" in caplog.text
    assert "consumer stopped" in caplog.text


@pytest.mark.asyncio
async def test_background_task_cancellation_is_not_logged_as_error(caplog):
    async def wait_forever():
        await asyncio.Event().wait()

    task = asyncio.create_task(wait_forever())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with caplog.at_level(logging.INFO, logger="app.main"):
        _log_background_task_completion(task, task_name="stock-reservation-consumer")

    assert "Фоновая задача остановлена" in caplog.text
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
