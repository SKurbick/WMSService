"""Atomic idempotent fill of a flat container from loose stock."""

from collections import defaultdict
from decimal import Decimal

from app.core.container_operation_idempotency import ContainerOperationDisposition
from app.core.exceptions import (
    ContainerNotFoundError,
    ContainerOperationConflictError,
    ProductNotFoundError,
)
from app.core.schemas.container_operations import ContainerFillResponse
from app.core.kiz_errors import KizConflictError
from app.core.services.container_kiz_service import lock_requested_kiz


class ContainerFillService:
    def __init__(self, repository, idempotency):
        self.repository = repository
        self.idempotency = idempotency

    async def _checkpoint(self, name):
        """Test-only fault injection seam; production implementation is a no-op."""

    @staticmethod
    def _intent(data):
        return {
            "operation_type": "fill",
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
        return item.product_id, item.batch_number or ""

    async def fill(self, data):
        async with self.repository.pool.acquire() as conn:
            async with conn.transaction(isolation="read_committed"):
                acquired = await self.idempotency.acquire(
                    conn, data=data, intent=self._intent(data)
                )
                if acquired.disposition is ContainerOperationDisposition.REPLAY:
                    return ContainerFillResponse.model_validate(
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
                        "Fill разрешён только для flat container с direct location"
                    )
                if container["status"] not in {"empty", "open"}:
                    raise ContainerOperationConflictError(
                        f"Fill запрещён для container status '{container['status']}'"
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

                requested = defaultdict(lambda: Decimal(0))
                item_by_scope = {}
                for item in items:
                    key = self._scope_key(item)
                    requested[key] += item.quantity
                    item_by_scope[key] = item

                before_totals = {}
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
                    if state["loose_quantity"] < requested[key]:
                        raise ContainerOperationConflictError(
                            f"Недостаточно loose stock для товара '{item.product_id}'"
                        )
                    before_totals[key] = state["loose_quantity"] + state["contained_quantity"]

                selected_by_line = await lock_requested_kiz(
                    self.repository, conn, items, container, holder="loose"
                )
                for key in sorted(requested):
                    item = item_by_scope[key]
                    state = dict(await self.repository.check_scope(
                        conn, product_id=item.product_id, location_id=location_id,
                        batch_number=item.batch_number, qr_code=qr_code,
                        container_id=data.container_id,
                    ))
                    identified = await self.repository.count_active_loose_kiz(
                        conn, item.product_id, location_id
                    )
                    selected_count = sum(
                        len(selected_by_line[candidate.external_line_id])
                        for candidate in items if self._scope_key(candidate) == key
                    )
                    unidentified_needed = requested[key] - Decimal(selected_count)
                    if state["loose_quantity"] - Decimal(identified) < unidentified_needed:
                        raise KizConflictError(
                            f"Недостаточно unidentified loose stock для товара '{item.product_id}'",
                            error_code="INSUFFICIENT_UNIDENTIFIED_QUANTITY",
                        )
                for item in items:
                    await self.repository.transition_kiz_holder(
                        conn,
                        operation_items[item.external_line_id],
                        [row["kiz_id"] for row in selected_by_line[item.external_line_id]],
                        "fill",
                    )

                await self.repository.open_container(conn, data.container_id)
                await self._checkpoint("after_status_update")

                result_items = []
                for item in items:
                    operation_item_id = operation_items[item.external_line_id]
                    outgoing = await self.repository.create_outgoing_movement(
                        conn,
                        item=item,
                        location_id=location_id,
                        author=data.author,
                        operation_id=operation_id,
                        operation_item_id=operation_item_id,
                    )
                    await self._checkpoint("after_outgoing_movement")
                    incoming = await self.repository.create_incoming_movement(
                        conn,
                        item=item,
                        location_id=location_id,
                        qr_code=qr_code,
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
                        raise RuntimeError("Movement registry did not register fill movement")
                    attached = await self.repository.attach_movements(
                        conn,
                        operation_item_id=operation_item_id,
                        outgoing_ref=movement_refs[0],
                        incoming_ref=movement_refs[1],
                    )
                    if attached is None:
                        raise RuntimeError("Container fill movements already attached")
                    selected = selected_by_line[item.external_line_id]
                    await self.repository.create_kiz_links(
                        conn, [row["kiz_id"] for row in selected], movement_refs
                    )

                    await self.repository.upsert_content(
                        conn,
                        operation_item_id=operation_item_id,
                        container_id=data.container_id,
                        item=item,
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

                response = ContainerFillResponse(
                    operation_id=operation_id,
                    operation_type="fill",
                    source_system=data.source_system,
                    external_operation_id=data.external_operation_id,
                    container_id=data.container_id,
                    container_qr_code=qr_code,
                    items=result_items,
                )
                await self._checkpoint("before_result_save")
                payload = response.model_dump(mode="json")
                await self.idempotency.store_result(
                    conn, operation_id=operation_id, payload=payload
                )
                return response
