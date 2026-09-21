"""Stage 3C C1 additive request and idempotency contracts."""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.container_operation_idempotency import container_operation_fingerprint
from app.core.schemas.container_operations import ContainerExtractRequest, ContainerFillRequest


def payload(model, codes=None, quantity="2", batch=None):
    return model.model_validate({
        "source_system": "test", "external_operation_id": "c1", "author": "tester",
        "container_id": 1,
        "items": [{"external_line_id": "1", "product_id": "sku",
                   "quantity": quantity, "batch_number": batch,
                   **({} if codes is None else {"kiz_codes": codes})}],
    })


@pytest.mark.parametrize("model", [ContainerFillRequest, ContainerExtractRequest])
def test_legacy_request_defaults_to_empty_kiz_set(model):
    assert payload(model).items[0].kiz_codes == []


@pytest.mark.parametrize("model", [ContainerFillRequest, ContainerExtractRequest])
def test_duplicate_batch_and_excess_kiz_are_rejected(model):
    with pytest.raises(ValidationError):
        payload(model, ["K1", "K1"])
    with pytest.raises(ValidationError):
        payload(model, ["K1"], batch="LOT")
    with pytest.raises(ValidationError):
        payload(model, ["K1", "K2"], quantity="1")


def test_duplicate_kiz_across_items_is_rejected():
    request = {
        "source_system": "test", "external_operation_id": "c1", "author": "tester",
        "container_id": 1, "items": [
            {"external_line_id": "1", "product_id": "sku", "quantity": "1", "kiz_codes": ["K1"]},
            {"external_line_id": "2", "product_id": "sku2", "quantity": "1", "kiz_codes": ["K1"]},
        ],
    }
    with pytest.raises(ValidationError):
        ContainerFillRequest.model_validate(request)


def test_kiz_order_is_not_idempotency_intent_but_set_is():
    base = {"operation_type": "fill", "container_id": 1, "items": [{
        "external_line_id": "1", "product_id": "sku", "batch_number": None,
        "quantity": Decimal("2"), "kiz_codes": ["K1", "K2"],
    }]}
    reordered = {**base, "items": [{**base["items"][0], "kiz_codes": ["K2", "K1"]}]}
    changed = {**base, "items": [{**base["items"][0], "kiz_codes": ["K1"]}]}
    assert container_operation_fingerprint(base) == container_operation_fingerprint(reordered)
    assert container_operation_fingerprint(base) != container_operation_fingerprint(changed)


def test_c1_preflight_is_read_only_and_migration_has_no_business_effects():
    root = Path(__file__).resolve().parents[1]
    preflight = (root / "scripts/migrations/20260918_container_c1_kiz_preflight.sql").read_text().lower()
    migration = (root / "scripts/migrations/20260918_add_container_c1_kiz.sql").read_text().lower()
    assert "insert into wms.movements" not in preflight
    assert "update wms.inventory" not in preflight
    assert "delete from" not in preflight
    assert "insert into wms.movements" not in migration
    assert "update wms.inventory" not in migration
