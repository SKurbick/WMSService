"""Request, fingerprint and migration contract tests for container B2.2 extract."""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.container_operation_idempotency import container_operation_fingerprint
from app.core.schemas.container_operations import ContainerExtractRequest


def request(**overrides):
    payload = {
        "source_system": "manual",
        "external_operation_id": "extract-1",
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
def test_extract_quantity_validation(quantity):
    payload = request()
    payload["items"][0]["quantity"] = quantity
    with pytest.raises(ValidationError):
        ContainerExtractRequest.model_validate(payload)


def test_duplicate_line_id_is_rejected():
    payload = request()
    payload["items"] *= 2
    with pytest.raises(ValidationError, match="duplicate external_line_id"):
        ContainerExtractRequest.model_validate(payload)


def test_duplicate_exact_physical_scope_is_rejected():
    payload = request()
    payload["items"] = [
        {**payload["items"][0], "external_line_id": "1"},
        {**payload["items"][0], "external_line_id": "2"},
    ]
    with pytest.raises(ValidationError, match="duplicate product/batch scope"):
        ContainerExtractRequest.model_validate(payload)


def test_same_product_different_batches_is_allowed():
    payload = request()
    payload["items"] = [
        {**payload["items"][0], "external_line_id": "1", "batch_number": None},
        {**payload["items"][0], "external_line_id": "2", "batch_number": "A"},
    ]
    assert len(ContainerExtractRequest.model_validate(payload).items) == 2


def test_extract_fingerprint_is_order_independent_and_operation_specific():
    first = {
        "operation_type": "extract",
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
    reordered = {**first, "items": list(reversed(first["items"]))}
    fill = {**first, "operation_type": "fill"}
    assert container_operation_fingerprint(first) == container_operation_fingerprint(reordered)
    assert container_operation_fingerprint(first) != container_operation_fingerprint(fill)


def test_b22_migration_is_schema_protocol_only():
    sql = (
        (
            Path(__file__).resolve().parents[1]
            / "scripts/migrations/20260916_add_container_b22_extract.sql"
        )
        .read_text(encoding="utf-8")
        .lower()
    )
    before_function = sql.split("create function wms.apply_container_extract_content", 1)[0]
    assert "update wms.inventory" not in sql
    assert "insert into wms.movements" not in sql
    assert "update wms.container_contents" not in before_function
    assert "delete from wms.container_contents" not in before_function


def test_b22_preflight_is_read_only():
    sql = (
        (
            Path(__file__).resolve().parents[1]
            / "scripts/migrations/20260916_container_b22_extract_preflight.sql"
        )
        .read_text(encoding="utf-8")
        .lower()
    )
    assert "read only" in sql
    assert "insert into" not in sql
    assert "update wms." not in sql
    assert "delete from" not in sql
