"""Persistence for idempotent container physical operations."""

from app.infrastructure.database.queries import container_operations as queries


class ContainerOperationRepository:
    def __init__(self, pool):
        self.pool = pool

    async def try_create(
        self,
        conn,
        *,
        operation_type,
        container_id,
        source_system,
        external_operation_id,
        fingerprint,
        author,
    ):
        return await conn.fetchrow(
            queries.TRY_CREATE_OPERATION,
            operation_type,
            container_id,
            source_system,
            external_operation_id,
            fingerprint,
            author,
        )

    async def get_for_update(self, conn, *, operation_type, source_system, external_operation_id):
        return await conn.fetchrow(
            queries.GET_OPERATION_FOR_UPDATE,
            operation_type,
            source_system,
            external_operation_id,
        )

    async def create_item(self, conn, *, operation_id, item, container_id):
        return await conn.fetchrow(
            queries.CREATE_ITEM,
            operation_id,
            item.external_line_id,
            container_id,
            item.product_id,
            item.batch_number,
            item.quantity,
        )

    async def attach_movements(self, conn, *, operation_item_id, outgoing_ref, incoming_ref):
        return await conn.fetchrow(
            queries.ATTACH_MOVEMENTS,
            operation_item_id,
            outgoing_ref,
            incoming_ref,
        )

    async def store_result(self, conn, *, operation_id, result_payload):
        return await conn.fetchrow(queries.STORE_RESULT, operation_id, result_payload)

    async def lock_container(self, conn, container_id):
        return await conn.fetchrow(queries.LOCK_CONTAINER, container_id)

    async def get_products(self, conn, product_ids):
        return await conn.fetch(queries.GET_PRODUCTS, list(product_ids))

    async def lock_scope(self, conn, product_id, location_id, batch_number, qr_code):
        return await conn.fetch(queries.LOCK_SCOPE, product_id, location_id, batch_number, qr_code)

    async def create_outgoing_movement(
        self, conn, *, item, location_id, author, operation_id, operation_item_id
    ):
        return await conn.fetchrow(
            queries.CREATE_OUTGOING_MOVEMENT,
            item.product_id,
            location_id,
            item.quantity,
            item.batch_number,
            author,
            operation_id,
            operation_item_id,
        )

    async def create_incoming_movement(
        self,
        conn,
        *,
        item,
        location_id,
        qr_code,
        author,
        operation_id,
        operation_item_id,
    ):
        return await conn.fetchrow(
            queries.CREATE_INCOMING_MOVEMENT,
            item.product_id,
            location_id,
            item.quantity,
            item.batch_number,
            qr_code,
            author,
            operation_id,
            operation_item_id,
        )

    async def movement_ref(self, conn, movement):
        return await conn.fetchval(
            queries.GET_MOVEMENT_REF,
            movement["movement_id"],
            movement["created_at"],
        )

    async def upsert_content(self, conn, *, operation_item_id, container_id, item):
        await conn.fetchval(queries.AUTHORIZE_CONTENT_INSERT, str(operation_item_id))
        try:
            return await conn.fetchrow(
                queries.UPSERT_CONTENT,
                container_id,
                item.product_id,
                item.quantity,
                item.batch_number,
            )
        finally:
            await conn.fetchval(queries.CLEAR_CONTENT_AUTHORIZATION)

    async def open_container(self, conn, container_id):
        await conn.execute(queries.OPEN_CONTAINER, container_id)

    async def lock_content_scope(self, conn, *, container_id, item):
        return await conn.fetchrow(
            queries.LOCK_CONTENT_SCOPE,
            container_id,
            item.product_id,
            item.batch_number,
        )

    async def create_extract_outgoing_movement(
        self,
        conn,
        *,
        item,
        location_id,
        qr_code,
        author,
        operation_id,
        operation_item_id,
    ):
        return await conn.fetchrow(
            queries.CREATE_EXTRACT_OUTGOING_MOVEMENT,
            item.product_id,
            location_id,
            item.quantity,
            item.batch_number,
            qr_code,
            author,
            operation_id,
            operation_item_id,
        )

    async def create_extract_incoming_movement(
        self, conn, *, item, location_id, author, operation_id, operation_item_id
    ):
        return await conn.fetchrow(
            queries.CREATE_EXTRACT_INCOMING_MOVEMENT,
            item.product_id,
            location_id,
            item.quantity,
            item.batch_number,
            author,
            operation_id,
            operation_item_id,
        )

    async def extract_content(self, conn, *, operation_item_id):
        return await conn.fetchval(queries.APPLY_EXTRACT_CONTENT, operation_item_id)

    async def sync_container_status(self, conn, container_id):
        return await conn.fetchval(queries.SYNC_CONTAINER_STATUS, container_id)

    async def check_scope(
        self, conn, *, product_id, location_id, batch_number, qr_code, container_id
    ):
        return await conn.fetchrow(
            queries.CHECK_SCOPE,
            product_id,
            location_id,
            batch_number,
            qr_code,
            container_id,
        )

    async def create_snapshot_item(
        self,
        conn,
        *,
        operation_id,
        external_line_id,
        container_id,
        product_id,
        batch_number,
        quantity,
    ):
        return await conn.fetchrow(
            queries.CREATE_SNAPSHOT_ITEM,
            operation_id,
            external_line_id,
            container_id,
            product_id,
            batch_number,
            quantity,
        )

    async def attach_move_movement(self, conn, *, operation_item_id, movement_ref):
        return await conn.fetchrow(queries.ATTACH_MOVE_MOVEMENT, operation_item_id, movement_ref)

    async def get_location_id_by_code(self, conn, location_code):
        return await conn.fetchval(queries.GET_LOCATION_ID_BY_CODE, location_code)

    async def lock_location_contexts(self, conn, location_ids):
        return await conn.fetch(queries.LOCK_LOCATION_CONTEXTS, sorted(set(location_ids)))

    async def lock_active_contents(self, conn, container_id):
        return await conn.fetch(queries.LOCK_ACTIVE_CONTENTS, container_id)

    async def lock_move_inventory_scope(
        self, conn, *, product_id, location_ids, batch_number, qr_code
    ):
        return await conn.fetch(
            queries.LOCK_MOVE_INVENTORY_SCOPE,
            product_id,
            sorted(set(location_ids)),
            batch_number,
            qr_code,
        )

    async def check_move_scope(
        self,
        conn,
        *,
        product_id,
        from_location_id,
        to_location_id,
        batch_number,
        qr_code,
    ):
        return await conn.fetchrow(
            queries.CHECK_MOVE_SCOPE,
            product_id,
            from_location_id,
            to_location_id,
            batch_number,
            qr_code,
        )

    async def container_projection_is_valid(self, conn, *, container_id, qr_code, location_id):
        return await conn.fetchval(
            queries.CHECK_CONTAINER_PROJECTION, container_id, qr_code, location_id
        )

    async def create_move_movement(
        self,
        conn,
        *,
        product_id,
        from_location_id,
        to_location_id,
        quantity,
        batch_number,
        qr_code,
        author,
        operation_id,
        operation_item_id,
    ):
        return await conn.fetchrow(
            queries.CREATE_MOVE_MOVEMENT,
            product_id,
            from_location_id,
            to_location_id,
            quantity,
            batch_number,
            qr_code,
            author,
            operation_id,
            operation_item_id,
        )

    async def move_container(
        self, conn, *, operation_id, container_id, from_location_id, to_location_id
    ):
        await conn.fetchval(queries.AUTHORIZE_CONTAINER_MOVE, str(operation_id))
        try:
            return await conn.fetchval(
                queries.UPDATE_CONTAINER_LOCATION_CONTROLLED,
                container_id,
                to_location_id,
                from_location_id,
            )
        finally:
            await conn.fetchval(queries.CLEAR_CONTAINER_MOVE_AUTHORIZATION)

    async def lock_kiz_codes(self, conn, kiz_codes):
        if not kiz_codes:
            return []
        return await conn.fetch(queries.LOCK_KIZ_CODES, list(kiz_codes))

    async def lock_container_kiz(self, conn, container_id):
        return await conn.fetch(queries.LOCK_CONTAINER_KIZ, container_id)

    async def count_active_loose_kiz(self, conn, product_id, location_id):
        return await conn.fetchval(queries.COUNT_ACTIVE_LOOSE_KIZ, product_id, location_id)

    async def count_active_container_kiz(self, conn, product_id, container_id):
        return await conn.fetchval(queries.COUNT_ACTIVE_CONTAINER_KIZ, product_id, container_id)

    async def transition_kiz_holder(self, conn, operation_item_id, kiz_ids, direction):
        for kiz_id in kiz_ids:
            await conn.fetchval(
                queries.TRANSITION_KIZ_CONTAINER_HOLDER,
                operation_item_id,
                kiz_id,
                direction,
            )

    async def create_kiz_links(self, conn, kiz_ids, movement_refs):
        if kiz_ids:
            await conn.execute(queries.CREATE_KIZ_LINKS, list(kiz_ids), list(movement_refs))

    async def authorize_kiz_container_operation(self, conn, operation_id):
        await conn.fetchval(queries.SET_KIZ_CONTAINER_OPERATION, str(operation_id))

    async def clear_kiz_container_operation(self, conn):
        await conn.fetchval(queries.CLEAR_KIZ_CONTAINER_OPERATION)

    async def kiz_integrity(self, conn):
        return await conn.fetch(queries.CHECK_KIZ_INTEGRITY)
