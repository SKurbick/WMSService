"""Request and fingerprint contract tests for container B2.1 fill."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.container_operation_idempotency import container_operation_fingerprint
from app.core.schemas.container_operations import ContainerFillRequest


def request(**overrides):
    payload = {
        "source_system": "manual",
        "external_operation_id": "fill-1",
        "author": "operator",
        "container_id": 1,
        "items": [
            {
                "external_line_id": "1",
                "product_id": "sku",
                "quantity": "1.50",
                "batch_number": None,
            }
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("quantity", [0, "-1", 1.5, "0.001", "100000000"])
def test_fill_quantity_validation(quantity):
    payload = request()
    payload["items"][0]["quantity"] = quantity
    with pytest.raises(ValidationError):
        ContainerFillRequest.model_validate(payload)


def test_duplicate_line_id_is_rejected():
    payload = request()
    payload["items"] *= 2
    with pytest.raises(ValidationError):
        ContainerFillRequest.model_validate(payload)


def test_fingerprint_is_order_independent_and_decimal_exact():
    first = {
        "operation_type": "fill",
        "container_id": 1,
        "items": [
            {
                "external_line_id": "2",
                "product_id": "b",
                "batch_number": None,
                "quantity": Decimal("2.00"),
            },
            {
                "external_line_id": "1",
                "product_id": "a",
                "batch_number": "x",
                "quantity": Decimal("1.50"),
            },
        ],
    }
    second = {**first, "items": list(reversed(first["items"]))}
    assert container_operation_fingerprint(first) == container_operation_fingerprint(second)

    changed = {**first, "items": [dict(first["items"][0]), dict(first["items"][1])]}
    changed["items"][1]["quantity"] = Decimal("1.51")
    assert container_operation_fingerprint(first) != container_operation_fingerprint(changed)


def test_b21_migration_is_schema_protocol_only():
    from pathlib import Path

    sql = (
        (
            Path(__file__).resolve().parents[1]
            / "scripts/migrations/20260915_add_container_b21_fill.sql"
        )
        .read_text(encoding="utf-8")
        .lower()
    )
    before_functions = sql.split("create or replace function wms.sync_container_to_inventory", 1)[0]
    assert "update wms.inventory" not in before_functions
    assert "insert into wms.movements" not in before_functions
    assert "update wms.container_contents" not in before_functions
    assert "insert into wms.container_contents" not in before_functions


def test_b21_preflight_is_read_only():
    from pathlib import Path

    sql = (
        (
            Path(__file__).resolve().parents[1]
            / "scripts/migrations/20260915_container_b21_fill_preflight.sql"
        )
        .read_text(encoding="utf-8")
        .lower()
    )
    assert "read only" in sql
    assert "insert into" not in sql
    assert "update wms." not in sql
    assert "delete from" not in sql
