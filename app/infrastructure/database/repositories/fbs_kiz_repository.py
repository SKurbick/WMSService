"""Persistence for KIZ-aware task-level FBS shipment."""

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from asyncpg import Connection

from app.core.exceptions import FbsKizProcessingError


class FbsKizRepository:
    async def persist_bindings(
        self,
        connection: Connection,
        *,
        shipment_id: int,
        item_bindings: Sequence[tuple[int, str, Sequence[Any]]],
    ) -> dict[str, FbsKizProcessingError]:
        intents = sorted(
            (
                int(binding.assembly_task),
                binding.kiz_code,
                product_id,
                item_id,
            )
            for item_id, product_id, bindings in item_bindings
            for binding in bindings
        )
        if not intents:
            return {}

        task_ids = sorted({intent[0] for intent in intents})
        await connection.fetch(
            """
            SELECT task_id
            FROM public.assembly_task
            WHERE task_id = ANY($1::bigint[])
            ORDER BY task_id
            FOR UPDATE
            """,
            task_ids,
        )

        errors: dict[str, FbsKizProcessingError] = {}
        for task_id, kiz_code, product_id, item_id in intents:
            existing = await connection.fetchrow(
                """
                SELECT binding_id, task_id, kiz_code, product_id
                FROM wms.fbs_kiz_bindings
                WHERE task_id = $1 OR kiz_code = $2
                ORDER BY binding_id
                FOR UPDATE
                """,
                task_id,
                kiz_code,
            )
            if existing is not None:
                if existing["task_id"] != task_id:
                    errors[str(task_id)] = FbsKizProcessingError(
                        f"KIZ '{kiz_code}' уже связан с СЗ {existing['task_id']}",
                        error_code="KIZ_BINDING_CONFLICT",
                    )
                elif existing["kiz_code"] != kiz_code or existing["product_id"] != product_id:
                    errors[str(task_id)] = FbsKizProcessingError(
                        f"СЗ {task_id} уже связано с другим KIZ или product",
                        error_code="ASSEMBLY_TASK_BINDING_CONFLICT",
                    )
                continue

            already_shipped = await connection.fetchval(
                "SELECT is_shipped FROM public.assembly_task WHERE task_id = $1",
                task_id,
            )
            if already_shipped:
                errors[str(task_id)] = FbsKizProcessingError(
                    f"Нельзя ретроактивно связать KIZ с уже отгруженной СЗ {task_id}",
                    error_code="ASSEMBLY_TASK_BINDING_CONFLICT",
                )
                continue

            inserted = await connection.fetchrow(
                """
                INSERT INTO wms.fbs_kiz_bindings (
                    task_id, kiz_code, kiz_id, product_id,
                    first_shipment_id, first_item_id, state
                )
                SELECT $1, $2, kiz.kiz_id, $3, $4, $5, 'pending_resolution'
                FROM (SELECT 1) AS seed
                LEFT JOIN wms.kiz AS kiz ON kiz.kiz_code = $2
                ON CONFLICT DO NOTHING
                RETURNING binding_id
                """,
                task_id,
                kiz_code,
                product_id,
                shipment_id,
                item_id,
            )
            if inserted is None:
                winner = await connection.fetchrow(
                    """
                    SELECT task_id, kiz_code, product_id
                    FROM wms.fbs_kiz_bindings
                    WHERE task_id = $1 OR kiz_code = $2
                    ORDER BY binding_id
                    LIMIT 1
                    FOR UPDATE
                    """,
                    task_id,
                    kiz_code,
                )
                if winner is None or (
                    winner["task_id"] != task_id
                    or winner["kiz_code"] != kiz_code
                    or winner["product_id"] != product_id
                ):
                    code = (
                        "KIZ_BINDING_CONFLICT"
                        if winner is not None and winner["task_id"] != task_id
                        else "ASSEMBLY_TASK_BINDING_CONFLICT"
                    )
                    errors[str(task_id)] = FbsKizProcessingError(
                        f"Конкурентный конфликт binding для СЗ {task_id} и KIZ '{kiz_code}'",
                        error_code=code,
                    )
        return errors

    async def lock_bindings(self, connection: Connection, task_ids: Sequence[str]) -> dict[str, dict]:
        rows = await connection.fetch(
            """
            SELECT binding.binding_id, binding.task_id, binding.kiz_code,
                   COALESCE(binding.kiz_id, kiz.kiz_id) AS kiz_id,
                   binding.product_id, binding.state,
                   kiz.lifecycle_status, kiz.location_id, kiz.container_id,
                   kiz.product_id AS kiz_product_id
            FROM wms.fbs_kiz_bindings AS binding
            LEFT JOIN wms.kiz AS kiz ON kiz.kiz_code = binding.kiz_code
            WHERE binding.task_id = ANY($1::bigint[])
            ORDER BY binding.task_id
            FOR UPDATE OF binding
            """,
            [int(task_id) for task_id in task_ids],
        )
        for row in rows:
            if row["kiz_id"] is not None:
                await connection.execute(
                    """
                    UPDATE wms.fbs_kiz_bindings
                    SET kiz_id = $2, state = 'resolved', updated_at = now()
                    WHERE binding_id = $1 AND kiz_id IS NULL
                    """,
                    row["binding_id"],
                    row["kiz_id"],
                )
        kiz_ids = sorted({row["kiz_id"] for row in rows if row["kiz_id"] is not None})
        if kiz_ids:
            await connection.fetch(
                "SELECT kiz_id FROM wms.kiz WHERE kiz_id = ANY($1::bigint[]) ORDER BY kiz_id FOR UPDATE",
                kiz_ids,
            )
        refreshed = await connection.fetch(
            """
            SELECT binding.binding_id, binding.task_id, binding.kiz_code,
                   binding.kiz_id, binding.product_id, binding.state,
                   kiz.lifecycle_status, kiz.location_id, kiz.container_id,
                   kiz.product_id AS kiz_product_id
            FROM wms.fbs_kiz_bindings AS binding
            LEFT JOIN wms.kiz AS kiz ON kiz.kiz_id = binding.kiz_id
            WHERE binding.task_id = ANY($1::bigint[])
            ORDER BY binding.task_id
            """,
            [int(task_id) for task_id in task_ids],
        )
        return {str(row["task_id"]): dict(row) for row in refreshed}

    async def lock_source_scopes(
        self,
        connection: Connection,
        scopes: Sequence[tuple[int, str]],
    ) -> dict[tuple[int, str], dict]:
        result: dict[tuple[int, str], dict] = {}
        for location_id, product_id in sorted(set(scopes)):
            await connection.fetchval(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(
                        'fbs-kiz:' || CAST($1 AS bigint)::text || ':' || CAST($2 AS text),
                        0
                    )
                )
                """,
                location_id,
                product_id,
            )
            row = await connection.fetchrow(
                """
                SELECT inventory_id, quantity
                FROM wms.inventory
                WHERE product_id = $1 AND location_id = $2
                  AND status = 'available'
                  AND batch_number IS NULL AND container_code IS NULL
                FOR UPDATE
                """,
                product_id,
                location_id,
            )
            identified = await connection.fetchval(
                """
                SELECT count(*)::bigint FROM wms.kiz
                WHERE product_id = $1 AND location_id = $2
                  AND container_id IS NULL AND lifecycle_status = 'active'
                """,
                product_id,
                location_id,
            )
            result[(location_id, product_id)] = {
                "physical": row["quantity"] if row else 0,
                "identified": identified,
            }
        return result

    async def get_location_by_code(self, connection: Connection, code: str) -> dict:
        row = await connection.fetchrow(
            "SELECT location_id, location_code FROM wms.locations WHERE location_code = $1 FOR SHARE",
            code,
        )
        if row is None:
            raise FbsKizProcessingError(
                f"FBS location '{code}' не найдена",
                error_code="LOCATION_NOT_FOUND",
            )
        return dict(row)

    async def get_locations(self, connection: Connection, location_ids: Sequence[int]) -> dict[int, dict]:
        rows = await connection.fetch(
            """
            SELECT location_id, location_code
            FROM wms.locations
            WHERE location_id = ANY($1::bigint[])
            ORDER BY location_id
            FOR SHARE
            """,
            sorted(set(location_ids)),
        )
        return {row["location_id"]: dict(row) for row in rows}

    async def create_operation(
        self,
        connection: Connection,
        *,
        shipment_id: int,
        product_id: str,
        author: str,
        tasks: Sequence[dict],
    ) -> int:
        external_id = f"fbs:{shipment_id}:{product_id}"
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    [task["task_id"], task.get("kiz_code"), task["source_location_id"]]
                    for task in sorted(tasks, key=lambda value: int(value["task_id"]))
                ],
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        row = await connection.fetchrow(
            """
            INSERT INTO wms.kiz_operations (
                operation_type, source_system, external_operation_id,
                request_fingerprint, author, result_payload
            ) VALUES ('ship', 'fbs', $1, $2, $3, NULL)
            RETURNING operation_id
            """,
            external_id,
            fingerprint,
            author,
        )
        return row["operation_id"]

    async def create_operation_item(
        self, connection: Connection, operation_id: int, external_line_id: str
    ) -> int:
        return await connection.fetchval(
            """
            INSERT INTO wms.kiz_operation_items (operation_id, external_line_id, movement_ref)
            VALUES ($1, $2, NULL)
            RETURNING operation_item_id
            """,
            operation_id,
            external_line_id,
        )

    async def transition_kiz_to_shipped(
        self,
        connection: Connection,
        *,
        operation_item_id: int,
        binding: dict,
        source_location_id: int,
        movement_quantity: int,
    ) -> None:
        await connection.execute(
            "SELECT wms.ship_kiz($1, $2, $3, $4, $5)",
            operation_item_id,
            binding["kiz_id"],
            binding["product_id"],
            source_location_id,
            movement_quantity,
        )

    async def create_movement(
        self,
        connection: Connection,
        *,
        product_id: str,
        source_location_id: int,
        quantity: int,
        author: str,
        reason: str,
        operation_id: int,
        operation_item_id: int,
    ) -> dict:
        row = await connection.fetchrow(
            """
            INSERT INTO wms.movements (
                movement_type, product_id, from_location_id, to_location_id,
                quantity, batch_number, container_code, user_name, reason,
                source_type, source_id, source_item_id
            ) VALUES (
                'ship', $1, $2, NULL, $3, NULL, NULL, $4, $5,
                'kiz_operation', $6, $7
            )
            RETURNING movement_id, created_at
            """,
            product_id,
            source_location_id,
            quantity,
            author,
            reason,
            operation_id,
            operation_item_id,
        )
        movement_ref = await connection.fetchval(
            """
            SELECT movement_ref FROM wms.movement_registry
            WHERE movement_id = $1 AND movement_created_at = $2
            """,
            row["movement_id"],
            row["created_at"],
        )
        if movement_ref is None:
            raise FbsKizProcessingError(
                "Movement не зарегистрирован в movement_registry",
                error_code="KIZ_CONFLICT",
            )
        return {**dict(row), "movement_ref": movement_ref}

    async def link_operation_movement(
        self, connection: Connection, operation_item_id: int, movement_ref: int
    ) -> None:
        await connection.execute(
            "UPDATE wms.kiz_operation_items SET movement_ref = $2 WHERE operation_item_id = $1",
            operation_item_id,
            movement_ref,
        )

    async def record_shipped_kiz(
        self,
        connection: Connection,
        *,
        binding: dict,
        movement_ref: int,
        source_location_id: int,
        author: str,
        metadata: dict,
    ) -> None:
        await connection.execute(
            "INSERT INTO wms.kiz_movement_links (kiz_id, movement_ref) VALUES ($1, $2)",
            binding["kiz_id"],
            movement_ref,
        )
        await connection.execute(
            """
            INSERT INTO wms.kiz_events (
                kiz_id, event_type, from_status, to_status, product_id,
                location_id, container_id, author, reason, metadata, movement_ref
            ) VALUES ($1, 'shipped', $2, 'shipped', $3, $4, NULL, $5, NULL, $6::jsonb, $7)
            """,
            binding["kiz_id"],
            binding["lifecycle_status"],
            binding["product_id"],
            source_location_id,
            author,
            json.dumps(metadata, ensure_ascii=False),
            movement_ref,
        )
        await connection.execute(
            """
            UPDATE wms.fbs_kiz_bindings
            SET state = 'shipped', kiz_id = $2, updated_at = now()
            WHERE binding_id = $1
            """,
            binding["binding_id"],
            binding["kiz_id"],
        )

    async def complete_operation(
        self, connection: Connection, operation_id: int, items: Sequence[dict]
    ) -> None:
        await connection.execute(
            "UPDATE wms.kiz_operations SET result_payload = $2::jsonb WHERE operation_id = $1",
            operation_id,
            json.dumps({"items": list(items)}, ensure_ascii=False),
        )
