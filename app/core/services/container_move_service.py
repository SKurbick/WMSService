"""Atomic idempotent movement of a flat container between warehouse locations."""

from app.core.container_operation_idempotency import ContainerOperationDisposition
from app.core.exceptions import (
    ContainerNotFoundError,
    ContainerOperationConflictError,
    LocationNotFoundError,
)
from app.core.schemas.container_operations import ContainerMoveResponse
from app.core.kiz_errors import KizConflictError


class ContainerMoveService:
    def __init__(self, repository, idempotency):
        self.repository = repository
        self.idempotency = idempotency

    async def _checkpoint(self, name):
        """Test-only fault injection seam; production implementation is a no-op."""

    @staticmethod
    def _intent(data):
        return {
            "operation_type": "move",
            "container_id": data.container_id,
            "to_location_code": data.to_location_code,
        }

    @staticmethod
    def _scope_key(row):
        return row["product_id"], row["batch_number"] is not None, row["batch_number"] or ""

    async def move(self, data):
        async with self.repository.pool.acquire() as conn:
            async with conn.transaction(isolation="read_committed"):
                acquired = await self.idempotency.acquire(
                    conn, data=data, intent=self._intent(data)
                )
                if acquired.disposition is ContainerOperationDisposition.REPLAY:
                    return ContainerMoveResponse.model_validate(
                        acquired.operation["result_payload"]
                    )

                operation_id = acquired.operation["operation_id"]
                await self._checkpoint("after_operation_creation")
                container = await self.repository.lock_container(conn, data.container_id)
                if container is None:
                    raise ContainerNotFoundError(f"Контейнер с ID {data.container_id} не найден")
                container = dict(container)
                if container["parent_container_id"] is not None or container["location_id"] is None:
                    raise ContainerOperationConflictError(
                        "Move разрешён только для flat container с direct location"
                    )
                if container["status"] not in {"empty", "open", "sealed"}:
                    raise ContainerOperationConflictError(
                        f"Move запрещён для container status '{container['status']}'"
                    )
                await self._checkpoint("after_container_lock")

                destination_id = await self.repository.get_location_id_by_code(
                    conn, data.to_location_code
                )
                if destination_id is None:
                    raise LocationNotFoundError(
                        f"Локация с кодом '{data.to_location_code}' не найдена"
                    )
                source_id = container["location_id"]
                contexts = {
                    row["location_id"]: dict(row)
                    for row in await self.repository.lock_location_contexts(
                        conn, [source_id, destination_id]
                    )
                }
                if source_id not in contexts or destination_id not in contexts:
                    raise ContainerOperationConflictError(
                        "Не удалось определить warehouse для локации контейнера"
                    )
                source = contexts[source_id]
                destination = contexts[destination_id]
                if not destination["is_active"]:
                    raise ContainerOperationConflictError("Целевая локация неактивна")
                if source["warehouse_id"] != destination["warehouse_id"]:
                    raise ContainerOperationConflictError(
                        "Move контейнера разрешён только в пределах одного warehouse"
                    )

                qr_code = container["qr_code"]
                if source_id == destination_id:
                    result_items = []
                else:
                    contents = [
                        dict(row)
                        for row in await self.repository.lock_active_contents(
                            conn, data.container_id
                        )
                    ]
                    contents.sort(key=self._scope_key)
                    if not await self.repository.container_projection_is_valid(
                        conn,
                        container_id=data.container_id,
                        qr_code=qr_code,
                        location_id=source_id,
                    ):
                        raise ContainerOperationConflictError(
                            "Container contents и contained inventory расходятся"
                        )
                    operation_items = {}
                    for content in contents:
                        row = await self.repository.create_snapshot_item(
                            conn,
                            operation_id=operation_id,
                            external_line_id=f"content:{content['content_id']}",
                            container_id=data.container_id,
                            product_id=content["product_id"],
                            batch_number=content["batch_number"],
                            quantity=content["quantity"],
                        )
                        operation_items[content["content_id"]] = row["operation_item_id"]
                    await self._checkpoint("after_item_creation")

                    for content in contents:
                        await self.repository.lock_move_inventory_scope(
                            conn,
                            product_id=content["product_id"],
                            location_ids=[source_id, destination_id],
                            batch_number=content["batch_number"],
                            qr_code=qr_code,
                        )
                    contained_kiz = [
                        dict(row) for row in await self.repository.lock_container_kiz(
                            conn, data.container_id
                        )
                    ]
                    kiz_by_product = {}
                    for kiz in contained_kiz:
                        kiz_by_product.setdefault(kiz["product_id"], []).append(kiz)
                    for content in contents:
                        if content["batch_number"] is not None and kiz_by_product.get(content["product_id"]):
                            raise KizConflictError(
                                "Contained КИЗ нельзя сопоставить batch content scope",
                                error_code="KIZ_BATCH_NOT_SUPPORTED",
                            )
                        if (content["batch_number"] is None
                                and len(kiz_by_product.get(content["product_id"], [])) > content["quantity"]):
                            raise KizConflictError(
                                "Active contained KIZ превышают physical quantity",
                                error_code="KIZ_PHYSICAL_INTEGRITY_CONFLICT",
                            )
                    if any(kiz["product_id"] not in {c["product_id"] for c in contents if c["batch_number"] is None}
                           for kiz in contained_kiz):
                        raise KizConflictError(
                            "Contained КИЗ не сопоставлен batch-less content scope",
                            error_code="KIZ_PHYSICAL_INTEGRITY_CONFLICT",
                        )
                    for content in contents:
                        state = dict(
                            await self.repository.check_move_scope(
                                conn,
                                product_id=content["product_id"],
                                from_location_id=source_id,
                                to_location_id=destination_id,
                                batch_number=content["batch_number"],
                                qr_code=qr_code,
                            )
                        )
                        if (
                            state["from_quantity"] != content["quantity"]
                            or state["to_quantity"] != 0
                        ):
                            raise ContainerOperationConflictError(
                                "Contained inventory нельзя однозначно переместить"
                            )

                    result_items = []
                    await self.repository.authorize_kiz_container_operation(conn, operation_id)
                    for index, content in enumerate(contents):
                        operation_item_id = operation_items[content["content_id"]]
                        movement = await self.repository.create_move_movement(
                            conn,
                            product_id=content["product_id"],
                            from_location_id=source_id,
                            to_location_id=destination_id,
                            quantity=content["quantity"],
                            batch_number=content["batch_number"],
                            qr_code=qr_code,
                            author=data.author,
                            operation_id=operation_id,
                            operation_item_id=operation_item_id,
                        )
                        movement_ref = await self.repository.movement_ref(conn, movement)
                        if movement_ref is None:
                            raise RuntimeError("Movement registry did not register move movement")
                        attached = await self.repository.attach_move_movement(
                            conn,
                            operation_item_id=operation_item_id,
                            movement_ref=movement_ref,
                        )
                        if attached is None:
                            raise RuntimeError("Container move movement already attached")
                        selected = kiz_by_product.get(content["product_id"], []) if content["batch_number"] is None else []
                        await self.repository.create_kiz_links(
                            conn, [row["kiz_id"] for row in selected], [movement_ref]
                        )
                        if index == 0:
                            await self._checkpoint("after_first_movement")
                        result_items.append(
                            {
                                "product_id": content["product_id"],
                                "batch_number": content["batch_number"],
                                "quantity": content["quantity"],
                                "movement_refs": [movement_ref],
                                "kiz_codes": sorted(row["kiz_code"] for row in selected),
                            }
                        )

                    await self.repository.clear_kiz_container_operation(conn)
                    await self._checkpoint("after_inventory_projection")
                    moved_to = await self.repository.move_container(
                        conn,
                        operation_id=operation_id,
                        container_id=data.container_id,
                        from_location_id=source_id,
                        to_location_id=destination_id,
                    )
                    if moved_to != destination_id:
                        raise ContainerOperationConflictError(
                            "Локация контейнера изменилась конкурентно"
                        )
                    await self._checkpoint("after_location_update")
                    if not await self.repository.container_projection_is_valid(
                        conn,
                        container_id=data.container_id,
                        qr_code=qr_code,
                        location_id=destination_id,
                    ):
                        raise ContainerOperationConflictError(
                            "Итоговый container projection invariant нарушен"
                        )

                response = ContainerMoveResponse(
                    operation_id=operation_id,
                    operation_type="move",
                    source_system=data.source_system,
                    external_operation_id=data.external_operation_id,
                    container_id=data.container_id,
                    container_qr_code=qr_code,
                    container_status=container["status"],
                    from_location_code=source["location_code"],
                    to_location_code=destination["location_code"],
                    items=result_items,
                )
                await self._checkpoint("before_result_save")
                await self.idempotency.store_result(
                    conn, operation_id=operation_id, payload=response.model_dump(mode="json")
                )
                return response
