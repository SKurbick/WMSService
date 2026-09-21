"""Atomic idempotent unpacking of every active scope in an open flat container."""

from dataclasses import dataclass
from decimal import Decimal

from app.core.container_operation_idempotency import ContainerOperationDisposition
from app.core.exceptions import ContainerNotFoundError, ContainerOperationConflictError
from app.core.schemas.container_operations import ContainerUnpackAllResponse
from app.core.kiz_errors import KizConflictError
from app.core.services.container_kiz_service import group_container_kiz


@dataclass(frozen=True)
class _SnapshotItem:
    external_line_id: str
    product_id: str
    batch_number: str | None
    quantity: Decimal


class ContainerUnpackAllService:
    def __init__(self, repository, idempotency):
        self.repository = repository
        self.idempotency = idempotency

    async def _checkpoint(self, name):
        """Test-only fault injection seam; production implementation is a no-op."""

    @staticmethod
    def _intent(data):
        return {"operation_type": "unpack_all", "container_id": data.container_id}

    @staticmethod
    def _scope_key(row):
        return row["product_id"], row["batch_number"] is not None, row["batch_number"] or ""

    async def unpack_all(self, data):
        async with self.repository.pool.acquire() as conn:
            async with conn.transaction(isolation="read_committed"):
                acquired = await self.idempotency.acquire(
                    conn, data=data, intent=self._intent(data)
                )
                if acquired.disposition is ContainerOperationDisposition.REPLAY:
                    return ContainerUnpackAllResponse.model_validate(
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
                        "Unpack-all разрешён только для flat container с direct location"
                    )
                if container["status"] != "open":
                    raise ContainerOperationConflictError(
                        f"Unpack-all запрещён для container status '{container['status']}'"
                    )
                await self._checkpoint("after_container_lock")

                location_id = container["location_id"]
                qr_code = container["qr_code"]
                contexts = await self.repository.lock_location_contexts(conn, [location_id])
                if not contexts:
                    raise ContainerOperationConflictError(
                        "Не удалось определить location контейнера"
                    )
                location_code = contexts[0]["location_code"]
                contents = [
                    dict(row)
                    for row in await self.repository.lock_active_contents(conn, data.container_id)
                ]
                contents.sort(key=self._scope_key)
                if not contents:
                    raise ContainerOperationConflictError(
                        "Open container не содержит active contents"
                    )
                if not await self.repository.container_projection_is_valid(
                    conn,
                    container_id=data.container_id,
                    qr_code=qr_code,
                    location_id=location_id,
                ):
                    raise ContainerOperationConflictError(
                        "Container contents и contained inventory расходятся"
                    )

                items = []
                operation_items = {}
                for content in contents:
                    item = _SnapshotItem(
                        external_line_id=f"content:{content['content_id']}",
                        product_id=content["product_id"],
                        batch_number=content["batch_number"],
                        quantity=content["quantity"],
                    )
                    items.append(item)
                    row = await self.repository.create_snapshot_item(
                        conn,
                        operation_id=operation_id,
                        external_line_id=item.external_line_id,
                        container_id=data.container_id,
                        product_id=item.product_id,
                        batch_number=item.batch_number,
                        quantity=item.quantity,
                    )
                    operation_items[item.external_line_id] = row["operation_item_id"]
                await self._checkpoint("after_item_creation")

                before_totals = {}
                for item in items:
                    await self.repository.lock_scope(
                        conn, item.product_id, location_id, item.batch_number, qr_code
                    )
                    state = dict(
                        await self.repository.check_scope(
                            conn,
                            product_id=item.product_id,
                            location_id=location_id,
                            batch_number=item.batch_number,
                            qr_code=qr_code,
                            container_id=data.container_id,
                        )
                    )
                    if (
                        state["contained_quantity"] != item.quantity
                        or state["content_quantity"] != item.quantity
                    ):
                        raise ContainerOperationConflictError(
                            "Container scope изменился во время unpack-all"
                        )
                    before_totals[item.external_line_id] = (
                        state["loose_quantity"] + state["contained_quantity"]
                    )

                contained_kiz = [
                    dict(row) for row in await self.repository.lock_container_kiz(
                        conn, data.container_id
                    )
                ]
                selected_by_line = group_container_kiz(contained_kiz, items)
                for item in items:
                    selected = selected_by_line[item.external_line_id]
                    if Decimal(len(selected)) > item.quantity:
                        raise KizConflictError(
                            "Active contained KIZ превышают physical quantity",
                            error_code="KIZ_PHYSICAL_INTEGRITY_CONFLICT",
                        )
                    await self.repository.transition_kiz_holder(
                        conn,
                        operation_items[item.external_line_id],
                        [row["kiz_id"] for row in selected],
                        "extract",
                    )

                result_items = []
                for index, item in enumerate(items):
                    operation_item_id = operation_items[item.external_line_id]
                    outgoing = await self.repository.create_extract_outgoing_movement(
                        conn,
                        item=item,
                        location_id=location_id,
                        qr_code=qr_code,
                        author=data.author,
                        operation_id=operation_id,
                        operation_item_id=operation_item_id,
                    )
                    incoming = await self.repository.create_extract_incoming_movement(
                        conn,
                        item=item,
                        location_id=location_id,
                        author=data.author,
                        operation_id=operation_id,
                        operation_item_id=operation_item_id,
                    )
                    movement_refs = [
                        await self.repository.movement_ref(conn, outgoing),
                        await self.repository.movement_ref(conn, incoming),
                    ]
                    if any(ref is None for ref in movement_refs):
                        raise RuntimeError("Movement registry did not register unpack-all movement")
                    attached = await self.repository.attach_movements(
                        conn,
                        operation_item_id=operation_item_id,
                        outgoing_ref=movement_refs[0],
                        incoming_ref=movement_refs[1],
                    )
                    if attached is None:
                        raise RuntimeError("Container unpack-all movements already attached")
                    selected = selected_by_line[item.external_line_id]
                    await self.repository.create_kiz_links(
                        conn, [row["kiz_id"] for row in selected], movement_refs
                    )
                    if index == 0:
                        await self._checkpoint("after_first_scope_movements")
                    content = await self.repository.extract_content(
                        conn, operation_item_id=operation_item_id
                    )
                    if content != 0:
                        raise ContainerOperationConflictError(
                            "Unpack-all не удалил весь active content scope"
                        )
                    await self._checkpoint("after_contents_update")
                    result_items.append(
                        {
                            "product_id": item.product_id,
                            "batch_number": item.batch_number,
                            "quantity": item.quantity,
                            "movement_refs": movement_refs,
                            "kiz_codes": sorted(row["kiz_code"] for row in selected),
                        }
                    )

                container_status = await self.repository.sync_container_status(
                    conn, data.container_id
                )
                if container_status != "empty":
                    raise ContainerOperationConflictError("Unpack-all не перевёл контейнер в empty")
                await self._checkpoint("after_status_update")

                for item in items:
                    state = dict(
                        await self.repository.check_scope(
                            conn,
                            product_id=item.product_id,
                            location_id=location_id,
                            batch_number=item.batch_number,
                            qr_code=qr_code,
                            container_id=data.container_id,
                        )
                    )
                    if state["contained_quantity"] != 0 or state["content_quantity"] != 0:
                        raise ContainerOperationConflictError(
                            "Итоговый unpack-all projection invariant нарушен"
                        )
                    if (
                        state["loose_quantity"] + state["contained_quantity"]
                        != before_totals[item.external_line_id]
                    ):
                        raise ContainerOperationConflictError(
                            "Итоговый conservation invariant нарушен"
                        )
                    loose_identified = await self.repository.count_active_loose_kiz(
                        conn, item.product_id, location_id
                    )
                    if Decimal(loose_identified) > state["loose_quantity"]:
                        raise KizConflictError(
                            "Итоговый KIZ/physical invariant нарушен",
                            error_code="KIZ_PHYSICAL_INTEGRITY_CONFLICT",
                        )

                response = ContainerUnpackAllResponse(
                    operation_id=operation_id,
                    operation_type="unpack_all",
                    source_system=data.source_system,
                    external_operation_id=data.external_operation_id,
                    container_id=data.container_id,
                    container_qr_code=qr_code,
                    container_status="empty",
                    location_code=location_code,
                    items=result_items,
                )
                await self._checkpoint("before_result_save")
                await self.idempotency.store_result(
                    conn, operation_id=operation_id, payload=response.model_dump(mode="json")
                )
                return response
