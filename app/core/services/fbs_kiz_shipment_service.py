"""Atomic KIZ-aware FBS shipment for task-level processing."""

from collections import defaultdict
from decimal import Decimal
from typing import Any

from asyncpg import Connection

from app.core.exceptions import FbsKizProcessingError
from app.infrastructure.database.repositories.fbs_kiz_repository import FbsKizRepository


class FbsKizShipmentService:
    def __init__(self, repository: FbsKizRepository):
        self.repository = repository

    async def process(
        self,
        connection: Connection,
        *,
        shipment_id: int,
        product_id: str,
        author: str,
        fbs_location_code: str,
        results: list[dict[str, Any]],
        new_tasks: list[str],
        binding_errors: dict[str, FbsKizProcessingError] | None = None,
    ) -> list[dict[str, Any]]:
        if binding_errors:
            task_id = sorted(binding_errors, key=int)[0]
            raise binding_errors[task_id]

        bindings = await self.repository.lock_bindings(connection, new_tasks)
        fbs_location = await self.repository.get_location_by_code(connection, fbs_location_code)

        unknown = [bindings[task] for task in new_tasks if task in bindings and bindings[task]["kiz_id"] is None]
        if unknown:
            codes = ", ".join(sorted(binding["kiz_code"] for binding in unknown))
            raise FbsKizProcessingError(
                f"KIZ ещё не зарегистрированы: {codes}",
                error_code="KIZ_NOT_REGISTERED",
                retryable=True,
            )

        task_units: list[dict[str, Any]] = []
        location_ids = {fbs_location["location_id"]}
        for result in results:
            if result["outcome"] != "new":
                continue
            task_id = result["task_id"]
            binding = bindings.get(task_id)
            if binding is None:
                source_kind = "fbs_zone"
                source_location_id = fbs_location["location_id"]
            else:
                if binding["product_id"] != product_id or binding["kiz_product_id"] != product_id:
                    raise FbsKizProcessingError(
                        f"KIZ '{binding['kiz_code']}' не соответствует product '{product_id}'",
                        error_code="KIZ_PRODUCT_MISMATCH",
                    )
                lifecycle = binding["lifecycle_status"]
                if lifecycle == "registered":
                    if binding["location_id"] is not None or binding["container_id"] is not None:
                        raise FbsKizProcessingError(
                            f"Registered KIZ '{binding['kiz_code']}' имеет holder",
                            error_code="KIZ_CONFLICT",
                        )
                    source_kind = "fbs_zone"
                    source_location_id = fbs_location["location_id"]
                elif lifecycle == "active":
                    if binding["container_id"] is not None:
                        raise FbsKizProcessingError(
                            f"KIZ '{binding['kiz_code']}' находится в container",
                            error_code="KIZ_CONTAINER_SHIPMENT_UNSUPPORTED",
                        )
                    if binding["location_id"] is None:
                        raise FbsKizProcessingError(
                            f"Active KIZ '{binding['kiz_code']}' не имеет loose holder",
                            error_code="KIZ_CONFLICT",
                        )
                    source_kind = "active_loose"
                    source_location_id = binding["location_id"]
                else:
                    raise FbsKizProcessingError(
                        f"KIZ '{binding['kiz_code']}' имеет lifecycle '{lifecycle}'",
                        error_code="KIZ_TERMINAL",
                    )
                location_ids.add(source_location_id)

            unit = {
                "task_id": task_id,
                "result": result,
                "binding": binding,
                "kiz_code": binding["kiz_code"] if binding else None,
                "source_kind": source_kind,
                "source_location_id": source_location_id,
            }
            task_units.append(unit)

        locations = await self.repository.get_locations(connection, list(location_ids))
        if set(locations) != location_ids:
            raise FbsKizProcessingError(
                "Одна из source locations не найдена",
                error_code="LOCATION_NOT_FOUND",
            )

        by_source: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for unit in task_units:
            by_source[unit["source_location_id"]].append(unit)

        scope_state = await self.repository.lock_source_scopes(
            connection,
            [(location_id, product_id) for location_id in by_source],
        )
        for location_id, units in by_source.items():
            state = scope_state[(location_id, product_id)]
            physical = Decimal(state["physical"])
            identified = int(state["identified"])
            selected_active = sum(
                unit["binding"] is not None
                and unit["binding"]["lifecycle_status"] == "active"
                for unit in units
            )
            if physical < len(units) or physical - len(units) < identified - selected_active:
                raise FbsKizProcessingError(
                    f"Недостаточно остатка в '{locations[location_id]['location_code']}'",
                    error_code="KIZ_CONFLICT",
                    retryable=True,
                )

        operation_id = await self.repository.create_operation(
            connection,
            shipment_id=shipment_id,
            product_id=product_id,
            author=author,
            tasks=task_units,
        )
        operation_items: dict[int, int] = {}
        for location_id in sorted(by_source):
            operation_items[location_id] = await self.repository.create_operation_item(
                connection,
                operation_id,
                f"source:{location_id}:{product_id}",
            )

        for location_id, units in by_source.items():
            for unit in units:
                if unit["binding"] is None:
                    continue
                await self.repository.transition_kiz_to_shipped(
                    connection,
                    operation_item_id=operation_items[location_id],
                    binding=unit["binding"],
                    source_location_id=location_id,
                    movement_quantity=len(units),
                )

        movements: list[dict[str, Any]] = []
        operation_result_items: list[dict[str, Any]] = []
        for location_id in sorted(by_source):
            units = by_source[location_id]
            operation_item_id = operation_items[location_id]
            movement = await self.repository.create_movement(
                connection,
                product_id=product_id,
                source_location_id=location_id,
                quantity=len(units),
                author=author,
                reason=f"FBS shipment tasks: {[unit['task_id'] for unit in units]}",
                operation_id=operation_id,
                operation_item_id=operation_item_id,
            )
            await self.repository.link_operation_movement(
                connection,
                operation_item_id,
                movement["movement_ref"],
            )
            operation_result_items.append(
                {
                    "external_line_id": f"source:{location_id}:{product_id}",
                    "product_id": product_id,
                    "from_location_code": locations[location_id]["location_code"],
                    "quantity": len(units),
                    "movement_ref": movement["movement_ref"],
                }
            )
            for unit in units:
                binding = unit["binding"]
                if binding is not None:
                    await self.repository.record_shipped_kiz(
                        connection,
                        binding=binding,
                        movement_ref=movement["movement_ref"],
                        source_location_id=location_id,
                        author=author,
                        metadata={
                            "shipment_id": shipment_id,
                            "assembly_task": unit["task_id"],
                            "operation_id": operation_id,
                        },
                    )
                unit["result"].update(
                    outcome="written_off",
                    effect_quantity=1,
                    movement_id=movement["movement_id"],
                    movement_created_at=movement["created_at"],
                    movement_ref=movement["movement_ref"],
                    kiz_binding_id=binding["binding_id"] if binding else None,
                    source_kind=unit["source_kind"],
                    source_location_id=location_id,
                    source_container_id=None,
                    reason="СЗ списано KIZ-aware FBS movement",
                )
            movements.append(movement)

        await self.repository.complete_operation(connection, operation_id, operation_result_items)
        return movements
