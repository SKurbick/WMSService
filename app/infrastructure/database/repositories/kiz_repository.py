"""All write steps use the caller's connection."""
import json
from app.infrastructure.database.queries import kiz as q


class KizRepository:
    def __init__(self, pool):
        self.pool = pool

    async def get_product(self, conn, product_id):
        return await conn.fetchrow(q.GET_PRODUCT, product_id)

    async def get_location(self, conn, location_code):
        return await conn.fetchrow(q.GET_LOCATION, location_code)

    async def get(self, conn, kiz_code, *, lock=False):
        return await conn.fetchrow(q.LOCK_KIZ if lock else q.GET_KIZ, kiz_code)

    async def lock_inventory(self, conn, product_id, location_id):
        return await conn.fetchrow(q.LOCK_INVENTORY, product_id, location_id)

    async def lock_container_inventory(self, conn, product_id, container_id):
        await conn.fetchrow(q.LOCK_CONTAINER_HOLDER, container_id)
        return await conn.fetchrow(q.LOCK_CONTAINER_INVENTORY, product_id, container_id)

    async def touch_inventory(self, conn, inventory_id):
        await conn.execute(q.TOUCH_INVENTORY, inventory_id)

    async def count_active(self, conn, product_id, location_id):
        return await conn.fetchval(q.COUNT_ACTIVE, product_id, location_id)

    async def insert(self, conn, data, location_id):
        return await conn.fetchval(
            q.INSERT_KIZ, data.kiz_code, data.product_id, location_id,
            data.author, json.dumps(data.metadata),
        )

    async def event(self, conn, kiz, event_type, from_status, to_status, data):
        await conn.execute(
            q.INSERT_EVENT, kiz['kiz_id'], event_type, from_status, to_status,
            kiz['product_id'], kiz['location_id'], kiz.get('container_id'), data.author,
            getattr(data, 'reason', None), json.dumps(data.metadata),
        )

    async def terminate(self, conn, kiz_id, status):
        await conn.execute(q.TERMINATE, kiz_id, status)

    async def summary(self, conn, product_id, location_id):
        return dict(await conn.fetchrow(q.SUMMARY, product_id, location_id))

    async def get_container(self, conn, container_id, container_qr_code):
        return await conn.fetchrow(q.GET_CONTAINER, container_id, container_qr_code)

    async def container_summary(self, conn, product_id, container_id):
        return dict(await conn.fetchrow(q.CONTAINER_SUMMARY, product_id, container_id))

    async def list(self, conn, product_id, location_code, lifecycle_status, container_id,
                   container_qr_code, limit, offset):
        filters = (product_id, location_code, lifecycle_status, container_id, container_qr_code)
        total = await conn.fetchval(q.COUNT_KIZ, *filters)
        rows = await conn.fetch(q.LIST_KIZ, *filters, limit, offset)
        return dict(items=[dict(row) for row in rows], total=total, limit=limit, offset=offset)

    async def events(self, conn, kiz_id, limit, offset):
        total = await conn.fetchval(q.COUNT_EVENTS, kiz_id)
        rows = await conn.fetch(q.LIST_EVENTS, kiz_id, limit, offset)
        return dict(items=[dict(row) for row in rows], total=total, limit=limit, offset=offset)

    async def integrity(self, conn):
        return [dict(row) for row in await conn.fetch(q.INTEGRITY, None)]
