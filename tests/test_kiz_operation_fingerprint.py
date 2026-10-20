"""Deterministic physical-intent fingerprint tests."""

from decimal import Decimal

import pytest

from app.core.kiz_operation_idempotency import (
    canonical_kiz_operation_json,
    kiz_operation_fingerprint,
)


def transfer_intent(**overrides):
    value = {
        "operation_type": "transfer",
        "items": [
            {
                "external_line_id": "line-1",
                "product_id": "sku",
                "from_location": "A",
                "to_location": "B",
                "quantity": Decimal("1.00"),
                "kiz_codes": ["KIZ-A", "KIZ-B"],
            }
        ],
    }
    value.update(overrides)
    return value


def test_same_request_and_decimal_representations_have_same_fingerprint():
    first = transfer_intent()
    second = transfer_intent()
    second["items"][0]["quantity"] = Decimal("1.0")
    assert kiz_operation_fingerprint(first) == kiz_operation_fingerprint(second)
    assert canonical_kiz_operation_json(first) == canonical_kiz_operation_json(second)


@pytest.mark.parametrize(
    "field,value",
    [
        ("product_id", "other"),
        ("quantity", Decimal("2")),
        ("from_location", "OTHER"),
        ("to_location", "OTHER"),
        ("kiz_codes", ["KIZ-A", "KIZ-C"]),
    ],
)
def test_physical_intent_changes_fingerprint(field, value):
    original = transfer_intent()
    changed = transfer_intent()
    changed["items"][0][field] = value
    assert kiz_operation_fingerprint(original) != kiz_operation_fingerprint(changed)


def test_kiz_and_item_order_do_not_change_fingerprint():
    first = transfer_intent()
    first["items"].append(
        {
            "external_line_id": "line-2",
            "product_id": "other",
            "from_location": "A",
            "to_location": "B",
            "quantity": Decimal("3"),
            "kiz_codes": ["KIZ-C"],
        }
    )
    second = {
        "items": list(reversed(first["items"])),
        "operation_type": "transfer",
    }
    second["items"][1] = dict(second["items"][1])
    second["items"][1]["kiz_codes"] = ["KIZ-B", "KIZ-A"]
    assert kiz_operation_fingerprint(first) == kiz_operation_fingerprint(second)


def test_unicode_is_deterministic_and_preserved_in_canonical_json():
    intent = transfer_intent()
    intent["items"][0]["kiz_codes"] = ["КИЗ-ёж", "КИЗ-Я"]
    assert kiz_operation_fingerprint(intent) == kiz_operation_fingerprint(intent)
    assert "КИЗ-ёж" in canonical_kiz_operation_json(intent)


def test_operation_type_changes_fingerprint():
    shipment = transfer_intent(operation_type="ship")
    assert kiz_operation_fingerprint(transfer_intent()) != kiz_operation_fingerprint(
        shipment
    )


@pytest.mark.parametrize(
    "mutation,error",
    [
        (lambda value: value["items"][0].update(quantity=1.0), "float"),
        (
            lambda value: value["items"][0].update(kiz_codes=["KIZ-A", "KIZ-A"]),
            "duplicate KIZ",
        ),
        (
            lambda value: value["items"].append(dict(value["items"][0])),
            "duplicate external_line_id",
        ),
        (lambda value: value.update(comment="bad\x00json"), "NUL"),
        (lambda value: value.update(comment="bad\ud800json"), "Unicode"),
    ],
)
def test_invalid_canonical_input_is_rejected(mutation, error):
    intent = transfer_intent()
    mutation(intent)
    with pytest.raises(ValueError, match=error):
        kiz_operation_fingerprint(intent)
