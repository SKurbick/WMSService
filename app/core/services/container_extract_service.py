"""Atomic idempotent extraction from a flat container into loose stock."""

from decimal import Decimal

from app.core.container_operation_idempotency import ContainerOperationDisposition
from app.core.exceptions import (
    ContainerNotFoundError,
    ContainerOperationConflictError,
    ProductNotFoundError,
)
from app.core.schemas.container_operations import ContainerExtractResponse
from app.core.kiz_errors import KizConflictError
from app.core.services.container_kiz_service import lock_requested_kiz


class ContainerExtractService:
    def __init__(self, repository, idempotency):
        self.repository = repository
        self.idempotency = idempotency

    async def _checkpoint(self, name):
        """Test-only fault injection seam; production implementation is a no-op."""

    @staticmethod
    def _intent(data):
        return {
            "operation_type": "extract",
            "container_id": data.container_id,
            "items": [
                {
                    "external_line_id": item.external_line_id,
                    "product_id": item.product_id,
                    "batch_number": item.batch_number,
                    "quantity": item.quantity,
                    "kiz_codes": sorted(item.kiz_codes),
                }
                for item in data.items
            ],
        }

    @staticmethod
    def _scope_key(item):
        return item.product_id, item.batch_number is not None, item.batch_number or ""

    async def extract(self, data):
        async with self.repository.pool.acquire() as conn:
            async with conn.transaction(isolation="read_committed"):
                acquired = await self.idempotency.acquire(
                    conn, data=data, intent=self._intent(data)
                )
                if acquired.disposition is ContainerOperationDisposition.REPLAY:
                    return ContainerExtractResponse.model_validate(
                        acquired.operation["result_payload"]
                    )

                operation_id = acquired.operation["operation_id"]
                await self._checkpoint("after_operation_creation")

                items = sorted(data.items, key=lambda item: item.external_line_id)
                container = await self.repository.lock_container(conn, data.container_id)
                if container is None:
                    raise ContainerNotFoundError(f"Контейнер с ID {data.container_id} не найден")
                container = dict(container)
                if container["parent_container_id"] is not None or container["location_id"] is None:
                    raise ContainerOperationConflictError(
                        "Extract разрешён только для flat container с direct location"
                    )
                if container["status"] != "open":
                    raise ContainerOperationConflictError(
                        f"Extract запрещён для container status '{container['status']}'"
                    )
                await self._checkpoint("after_container_lock")

                product_ids = sorted({item.product_id for item in items})
                found = {row["id"] for row in await self.repository.get_products(conn, product_ids)}
                missing = set(product_ids) - found
                if missing:
                    raise ProductNotFoundError(f"Товар '{sorted(missing)[0]}' не найден")

                operation_items = {}
                for item in items:
                    row = await self.repository.create_item(
                        conn,
                        operation_id=operation_id,
                        item=item,
                        container_id=data.container_id,
                    )
                    operation_items[item.external_line_id] = row["operation_item_id"]
                await self._checkpoint("after_item_creation")

                requested = {self._scope_key(item): item.quantity for item in items}
                item_by_scope = {self._scope_key(item): item for item in items}
                location_id = container["location_id"]
                qr_code = container["qr_code"]

                for key in sorted(requested):
                    item = item_by_scope[key]
                    await self.repository.lock_scope(
                        conn,
                        item.product_id,
                        location_id,
                        item.batch_number,
                        qr_code,
                    )
                for key in sorted(requested):
                    item = item_by_scope[key]
                    await self.repository.lock_content_scope(
                        conn, container_id=data.container_id, item=item
                    )

                selected_by_line = await lock_requested_kiz(
                    self.repository, conn, items, container, holder="container"
                )
                before_totals = {}
                for key in sorted(requested):
                    item = item_by_scope[key]
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
                    if state["contained_quantity"] != state["content_quantity"]:
                        raise ContainerOperationConflictError(
                            "Container contents и contained inventory расходятся"
                        )
                    if state["contained_quantity"] < requested[key]:
                        raise ContainerOperationConflictError(
                            f"Недостаточно contained stock для товара '{item.product_id}'"
                        )
                    identified = await self.repository.count_active_container_kiz(
                        conn, item.product_id, data.container_id
                    )
                    unidentified_needed = item.quantity - Decimal(
                        len(selected_by_line[item.external_line_id])
                    )
                    if state["contained_quantity"] - Decimal(identified) < unidentified_needed:
                        raise KizConflictError(
                            f"Недостаточно unidentified contained stock для товара '{item.product_id}'",
                            error_code="INSUFFICIENT_UNIDENTIFIED_QUANTITY",
                        )
                    before_totals[key] = state["loose_quantity"] + state["contained_quantity"]
                for item in items:
                    await self.repository.transition_kiz_holder(
                        conn,
                        operation_items[item.external_line_id],
                        [row["kiz_id"] for row in selected_by_line[item.external_line_id]],
                        "extract",
                    )

                result_items = []
                for item in items:
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
                    await self._checkpoint("after_outgoing_movement")
                    incoming = await self.repository.create_extract_incoming_movement(
                        conn,
                        item=item,
                        location_id=location_id,
                        author=data.author,
                        operation_id=operation_id,
                        operation_item_id=operation_item_id,
                    )
                    await self._checkpoint("after_incoming_movement")

                    movement_refs = [
                        await self.repository.movement_ref(conn, outgoing),
                        await self.repository.movement_ref(conn, incoming),
                    ]
                    if any(ref is None for ref in movement_refs):
                        raise RuntimeError("Movement registry did not register extract movement")
                    attached = await self.repository.attach_movements(
                        conn,
                        operation_item_id=operation_item_id,
                        outgoing_ref=movement_refs[0],
                        incoming_ref=movement_refs[1],
                    )
                    if attached is None:
                        raise RuntimeError("Container extract movements already attached")
                    selected = selected_by_line[item.external_line_id]
                    await self.repository.create_kiz_links(
                        conn, [row["kiz_id"] for row in selected], movement_refs
                    )

                    content = await self.repository.extract_content(
                        conn, operation_item_id=operation_item_id
                    )
                    if content is None:
                        raise ContainerOperationConflictError(
                            f"Active content scope для товара '{item.product_id}' изменился"
                        )
                    await self._checkpoint("after_contents_update")
                    result_items.append(
                        {
                            "external_line_id": item.external_line_id,
                            "product_id": item.product_id,
                            "batch_number": item.batch_number,
                            "quantity": item.quantity,
                            "movement_refs": movement_refs,
                            "kiz_codes": sorted(item.kiz_codes),
                        }
                    )

                container_status = await self.repository.sync_container_status(
                    conn, data.container_id
                )
                if container_status not in {"empty", "open"}:
                    raise RuntimeError("Container extract did not produce a valid status")
                await self._checkpoint("after_status_update")

                for key in sorted(requested):
                    item = item_by_scope[key]
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
                    if state["contained_quantity"] != state["content_quantity"]:
                        raise ContainerOperationConflictError(
                            "Итоговый container contents invariant нарушен"
                        )
                    if state["loose_quantity"] + state["contained_quantity"] != before_totals[key]:
                        raise ContainerOperationConflictError(
                            "Итоговый conservation invariant нарушен"
                        )
                    if state["contained_quantity"] < Decimal(0):
                        raise ContainerOperationConflictError(
                            "Итоговый contained stock не может быть отрицательным"
                        )
                    loose_identified = await self.repository.count_active_loose_kiz(
                        conn, item.product_id, location_id
                    )
                    contained_identified = await self.repository.count_active_container_kiz(
                        conn, item.product_id, data.container_id
                    )
                    if (Decimal(loose_identified) > state["loose_quantity"]
                            or Decimal(contained_identified) > state["contained_quantity"]
                            or Decimal(contained_identified) > state["content_quantity"]):
                        raise KizConflictError(
                            "Итоговый KIZ/physical invariant нарушен",
                            error_code="KIZ_PHYSICAL_INTEGRITY_CONFLICT",
                        )

                response = ContainerExtractResponse(
                    operation_id=operation_id,
                    operation_type="extract",
                    source_system=data.source_system,
                    external_operation_id=data.external_operation_id,
                    container_id=data.container_id,
                    container_qr_code=qr_code,
                    container_status=container_status,
                    items=result_items,
                )
                await self._checkpoint("before_result_save")
                await self.idempotency.store_result(
                    conn,
                    operation_id=operation_id,
                    payload=response.model_dump(mode="json"),
                )
                return response
