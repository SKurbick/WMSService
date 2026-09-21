"""Persistence primitives for a caller-owned KIZ operation transaction."""

from app.infrastructure.database.queries import kiz_operations as queries


class KizOperationRepository:
    async def try_create(
        self,
        conn,
        *,
        operation_type,
        source_system,
        external_operation_id,
        request_fingerprint,
        author,
    ):
        return await conn.fetchrow(
            queries.TRY_CREATE_OPERATION,
            operation_type,
            source_system,
            external_operation_id,
            request_fingerprint,
            author,
        )

    async def get_for_update(
        self, conn, *, source_system, operation_type, external_operation_id
    ):
        return await conn.fetchrow(
            queries.GET_OPERATION_FOR_UPDATE,
            source_system,
            operation_type,
            external_operation_id,
        )

    async def create_item(self, conn, *, operation_id, external_line_id):
        return await conn.fetchrow(
            queries.CREATE_ITEM, operation_id, external_line_id
        )

    async def attach_movement(self, conn, *, operation_item_id, movement_ref):
        return await conn.fetchrow(
            queries.ATTACH_MOVEMENT, operation_item_id, movement_ref
        )

    async def store_result(self, conn, *, operation_id, result_payload):
        return await conn.fetchrow(
            queries.STORE_RESULT, operation_id, result_payload
        )

    async def get(self, conn, *, operation_id):
        return await conn.fetchrow(queries.GET_OPERATION, operation_id)
