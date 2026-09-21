"""Atomic explicit shipment of stock with an explicit subset of active KIZ."""

from collections import defaultdict
from decimal import Decimal

from app.core.exceptions import LocationNotFoundError, ProductNotFoundError
from app.core.kiz_errors import KizShipConflictError
from app.core.kiz_operation_idempotency import KizOperationDisposition
from app.core.schemas.kiz_operations import KizShipResponse


class KizShipService:
    def __init__(self, repository, idempotency):
        self.repository = repository
        self.idempotency = idempotency

    async def _checkpoint(self, name):
        """Test-only fault injection seam; production implementation is a no-op."""

    @staticmethod
    def _intent(data):
        return {
            "operation_type": "ship",
            "items": [
                {
                    "external_line_id": item.external_line_id,
                    "product_id": item.product_id,
                    "from_location": item.from_location_code,
                    "quantity": item.quantity,
                    "kiz_codes": item.kiz_codes,
                }
                for item in data.items
            ],
        }

    async def ship(self, data):
        async with self.repository.pool.acquire() as conn:
            async with conn.transaction(isolation="read_committed"):
                acquired = await self.idempotency.acquire(
                    conn,
                    operation_type="ship",
                    source_system=data.source_system,
                    external_operation_id=data.external_operation_id,
                    author=data.author,
                    intent=self._intent(data),
                )
                if acquired.disposition is KizOperationDisposition.REPLAY:
                    return KizShipResponse.model_validate(
                        acquired.operation["result_payload"]
                    )

                operation_id = acquired.operation["operation_id"]
                await self._checkpoint("after_operation_creation")
                items = sorted(data.items, key=lambda item: item.external_line_id)
                operation_items = {}
                for item in items:
                    row = await self.idempotency.create_item(
                        conn,
                        operation_id=operation_id,
                        external_line_id=item.external_line_id,
                    )
                    operation_items[item.external_line_id] = row["operation_item_id"]

                product_ids = sorted({item.product_id for item in items})
                found_products = {
                    row["id"]
                    for row in await self.repository.get_products(conn, product_ids)
                }
                missing_products = set(product_ids) - found_products
                if missing_products:
                    raise ProductNotFoundError(
                        f"Товар '{sorted(missing_products)[0]}' не найден"
                    )

                location_codes = sorted({item.from_location_code for item in items})
                locations = {
                    row["location_code"]: dict(row)
                    for row in await self.repository.get_locations(conn, location_codes)
                }
                missing_locations = set(location_codes) - set(locations)
                if missing_locations:
                    raise LocationNotFoundError(
                        f"Локация '{sorted(missing_locations)[0]}' не найдена"
                    )

                await self.repository.lock_locations(
                    conn, sorted(row["location_id"] for row in locations.values())
                )

                source_totals = defaultdict(
                    lambda: {"quantity": Decimal(0), "kiz": 0}
                )
                for item in items:
                    source_id = locations[item.from_location_code]["location_id"]
                    scope = (item.product_id, source_id)
                    source_totals[scope]["quantity"] += item.quantity
                    source_totals[scope]["kiz"] += len(item.kiz_codes)

                inventories = {}
                for scope in sorted(source_totals):
                    row = await self.repository.lock_inventory(conn, *scope)
                    inventories[scope] = dict(row) if row else None

                all_codes = sorted(code for item in items for code in item.kiz_codes)
                kiz_rows = (
                    await self.repository.lock_kiz(conn, all_codes)
                    if all_codes else []
                )
                kiz_by_code = {row["kiz_code"]: dict(row) for row in kiz_rows}
                if set(all_codes) != set(kiz_by_code):
                    raise KizShipConflictError("Один или несколько КИЗ не найдены")

                for item in items:
                    source_id = locations[item.from_location_code]["location_id"]
                    for code in item.kiz_codes:
                        kiz = kiz_by_code[code]
                        if (
                            kiz["lifecycle_status"] != "active"
                            or kiz["closed_at"] is not None
                        ):
                            raise KizShipConflictError(f"КИЗ '{code}' не active")
                        if kiz["product_id"] != item.product_id:
                            raise KizShipConflictError(
                                f"КИЗ '{code}' принадлежит другому товару"
                            )
                        if kiz["location_id"] != source_id:
                            raise KizShipConflictError(
                                f"КИЗ '{code}' находится на другой локации"
                            )

                for (product_id, source_id), requested in source_totals.items():
                    inventory = inventories[(product_id, source_id)]
                    physical = inventory["quantity"] if inventory else Decimal(0)
                    identified = Decimal(
                        await self.repository.count_active(conn, product_id, source_id)
                    )
                    unidentified = physical - identified
                    if physical < requested["quantity"]:
                        raise KizShipConflictError(
                            "Недостаточно physical stock для shipment"
                        )
                    if requested["quantity"] - requested["kiz"] > unidentified:
                        raise KizShipConflictError(
                            "Недостаточно неидентифицированного по КИЗ остатка для shipment"
                        )

                result_items = []
                for item in items:
                    item_id = operation_items[item.external_line_id]
                    source_id = locations[item.from_location_code]["location_id"]
                    selected = sorted(
                        (kiz_by_code[code] for code in item.kiz_codes),
                        key=lambda row: row["kiz_id"],
                    )
                    for kiz in selected:
                        await self.repository.ship_kiz(
                            conn, item_id, kiz["kiz_id"], item.product_id,
                            source_id, item.quantity,
                        )
                    await self._checkpoint("after_kiz_lifecycle_transition")

                    reason = "Явная отгрузка товара с учётом КИЗ"
                    movement = await self.repository.create_movement(
                        conn,
                        product_id=item.product_id,
                        source_id=source_id,
                        quantity=item.quantity,
                        author=data.author,
                        reason=reason,
                        operation_id=operation_id,
                        item_id=item_id,
                    )
                    await self._checkpoint("after_movement_insert")
                    movement_ref = await self.repository.movement_ref(
                        conn, movement["movement_id"], movement["created_at"]
                    )
                    if movement_ref is None:
                        raise RuntimeError(
                            "movement registry trigger did not register movement"
                        )
                    await self._checkpoint("after_movement_ref")
                    await self.idempotency.attach_movement(
                        conn, operation_item_id=item_id, movement_ref=movement_ref
                    )
                    await self._checkpoint("after_movement_attachment")
                    kiz_ids = [row["kiz_id"] for row in selected]
                    await self.repository.create_links(conn, kiz_ids, movement_ref)
                    await self._checkpoint("after_kiz_movement_links")
                    await self.repository.create_shipped_events(
                        conn,
                        kiz_ids=kiz_ids,
                        product_id=item.product_id,
                        source_id=source_id,
                        movement_ref=movement_ref,
                        author=data.author,
                        reason=reason,
                    )
                    await self._checkpoint("after_shipped_events")
                    result_items.append({
                        "external_line_id": item.external_line_id,
                        "product_id": item.product_id,
                        "from_location_code": item.from_location_code,
                        "quantity": item.quantity,
                        "kiz_codes": sorted(item.kiz_codes),
                        "movement_ref": movement_ref,
                    })

                for product_id, location_id in sorted(source_totals):
                    state = await self.repository.check_scope(
                        conn, product_id, location_id
                    )
                    if state["physical_quantity"] < state["identified_quantity"]:
                        raise KizShipConflictError(
                            "Итоговый KIZ inventory invariant нарушен"
                        )

                response_payload = {
                    "operation_id": operation_id,
                    "operation_type": "ship",
                    "source_system": data.source_system,
                    "external_operation_id": data.external_operation_id,
                    "items": result_items,
                }
                await self._checkpoint("before_result_payload")
                await self.idempotency.store_successful_result(
                    conn, operation_id=operation_id, result_payload=response_payload
                )
                return KizShipResponse.model_validate(response_payload)
