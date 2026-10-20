from contextlib import asynccontextmanager

import pytest

from app.core.services.kiz_history_service import (
    KizHistoryIntegrityError,
    KizHistoryService,
)


class BrokenHistoryRepository:
    def __init__(self):
        self.pool = self

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self, **kwargs):
        assert kwargs == {"isolation": "repeatable_read", "readonly": True}
        yield

    async def current_state(self, conn, kiz_code):
        return {"kiz_id": 7, "kiz_code": kiz_code, "product_id": "sku"}

    async def timeline(self, conn, kiz_id):
        return [
            {
                "movement_ref": 15001,
                "registry_missing": False,
                "movement_missing": True,
            }
        ]


async def test_missing_linked_movement_is_not_hidden_or_repaired():
    service = KizHistoryService(BrokenHistoryRepository())

    with pytest.raises(KizHistoryIntegrityError, match="movement_ref=15001"):
        await service.get("BROKEN-KIZ")
