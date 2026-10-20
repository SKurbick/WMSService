"""Shared KIZ validation for controlled container operations."""

from decimal import Decimal

from app.core.kiz_errors import KizConflictError


def _conflict(code, message):
    raise KizConflictError(message, error_code=code)


async def lock_requested_kiz(repository, conn, items, container, *, holder):
    codes = sorted({code for item in items for code in item.kiz_codes})
    rows = [dict(row) for row in await repository.lock_kiz_codes(conn, codes)]
    by_code = {row["kiz_code"]: row for row in rows}
    if len(by_code) != len(codes):
        missing = next(code for code in codes if code not in by_code)
        _conflict("KIZ_NOT_FOUND", f"КИЗ '{missing}' не найден")

    result = {}
    for item in items:
        selected = []
        for code in sorted(item.kiz_codes):
            row = by_code[code]
            if row["lifecycle_status"] != "active" or row["closed_at"] is not None:
                _conflict("KIZ_NOT_ACTIVE", f"КИЗ '{code}' не active")
            if row["product_id"] != item.product_id:
                _conflict("KIZ_PRODUCT_MISMATCH", f"КИЗ '{code}' относится к другому товару")
            if holder == "loose" and not (
                row["location_id"] == container["location_id"]
                and row["container_id"] is None
            ):
                _conflict("KIZ_HOLDER_MISMATCH", f"КИЗ '{code}' не находится в исходной россыпи")
            if holder == "container" and not (
                row["location_id"] is None
                and row["container_id"] == container["container_id"]
            ):
                _conflict("KIZ_HOLDER_MISMATCH", f"КИЗ '{code}' не находится в контейнере")
            selected.append(row)
        result[item.external_line_id] = selected
    return result


async def require_unidentified(repository, conn, *, item, physical_quantity, location_id,
                               container_id, holder, selected_count):
    if holder == "loose":
        identified = await repository.count_active_loose_kiz(
            conn, item.product_id, location_id
        )
    else:
        identified = await repository.count_active_container_kiz(
            conn, item.product_id, container_id
        )
    unidentified = Decimal(physical_quantity) - Decimal(identified)
    needed = item.quantity - Decimal(selected_count)
    if unidentified < needed:
        _conflict(
            "INSUFFICIENT_UNIDENTIFIED_QUANTITY",
            f"Недостаточно unidentified quantity для товара '{item.product_id}'",
        )


def group_container_kiz(rows, items):
    by_product = {}
    for item in items:
        if item.batch_number is None:
            by_product[item.product_id] = item.external_line_id
    grouped = {item.external_line_id: [] for item in items}
    for row in rows:
        line_id = by_product.get(row["product_id"])
        if line_id is None:
            _conflict(
                "KIZ_PHYSICAL_INTEGRITY_CONFLICT",
                f"Active КИЗ '{row['kiz_code']}' не сопоставлен batch-less content scope",
            )
        grouped[line_id].append(row)
    return grouped
