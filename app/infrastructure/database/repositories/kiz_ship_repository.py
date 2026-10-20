"""Persistence for explicit KIZ shipment inside one supplied transaction."""

from decimal import Decimal

from app.infrastructure.database.queries import kiz_ship as q


class KizShipRepository:
    def __init__(self, pool):
        self.pool = pool

    async def get_products(self, conn, ids):
        return await conn.fetch(q.GET_PRODUCTS, list(ids))

    async def get_locations(self, conn, codes):
        return await conn.fetch(q.GET_LOCATIONS, list(codes))

    async def lock_locations(self, conn, ids):
        return await conn.fetch(q.LOCK_LOCATIONS, list(ids))

    async def lock_inventory(self, conn, product_id, location_id):
        return await conn.fetchrow(q.LOCK_INVENTORY, product_id, location_id)

    async def lock_kiz(self, conn, codes):
        return await conn.fetch(q.LOCK_KIZ, list(codes))

    async def count_active(self, conn, product_id, location_id):
        return await conn.fetchval(q.COUNT_ACTIVE, product_id, location_id)

    async def ship_kiz(self, conn, item_id, kiz_id, product_id, source_id, quantity):
        await conn.execute(
            q.CONTROLLED_SHIP_KIZ, item_id, kiz_id, product_id, source_id,
            Decimal(quantity),
        )

    async def create_movement(self, conn, *, product_id, source_id, quantity, author,
                              reason, operation_id, item_id):
        return await conn.fetchrow(
            q.CREATE_MOVEMENT, product_id, source_id, quantity, author, reason,
            operation_id, item_id,
        )

    async def movement_ref(self, conn, movement_id, created_at):
        return await conn.fetchval(q.GET_MOVEMENT_REF, movement_id, created_at)

    async def create_links(self, conn, kiz_ids, movement_ref):
        if kiz_ids:
            await conn.execute(q.CREATE_LINKS, list(kiz_ids), movement_ref)

    async def create_shipped_events(self, conn, *, kiz_ids, product_id, source_id,
                                    movement_ref, author, reason):
        if kiz_ids:
            await conn.execute(
                q.CREATE_SHIPPED_EVENTS, list(kiz_ids), product_id, source_id,
                movement_ref, author, reason,
            )

    async def check_scope(self, conn, product_id, location_id):
        return await conn.fetchrow(q.CHECK_SCOPE, product_id, location_id)
