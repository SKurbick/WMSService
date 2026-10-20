from pydantic import ValidationError
import pytest

from app.core.schemas.kiz_operations import KizTransferRequest


def valid():
    return {
        "source_system": "manual", "external_operation_id": "id", "author": "operator",
        "items": [{"external_line_id": "1", "product_id": "sku",
                   "from_location_code": "A", "to_location_code": "B",
                   "quantity": 2, "kiz_codes": ["K1", "K2"]}],
    }


@pytest.mark.parametrize("mutation", [
    lambda p: p["items"][0].update(quantity=0),
    lambda p: p["items"][0].update(to_location_code="A"),
    lambda p: p["items"][0].update(kiz_codes=["K1", "K1"]),
    lambda p: p["items"][0].update(quantity=1, kiz_codes=["K1", "K2"]),
    lambda p: p["items"][0].update(quantity="2"),
    lambda p: p["items"][0].update(quantity=2.0),
    lambda p: p["items"][0].update(quantity=1.5),
    lambda p: p.update(source_system="bad\x00value"),
    lambda p: p.update(author="\ud800"),
])
def test_malformed_transfer_is_422_schema_error(mutation):
    payload = valid()
    mutation(payload)
    with pytest.raises(ValidationError):
        KizTransferRequest.model_validate(payload)


def test_duplicate_kiz_across_items_is_rejected():
    payload = valid()
    payload["items"].append({
        "external_line_id": "2", "product_id": "sku",
        "from_location_code": "A", "to_location_code": "C",
        "quantity": 1, "kiz_codes": ["K1"],
    })
    with pytest.raises(ValidationError):
        KizTransferRequest.model_validate(payload)


def test_duplicate_external_line_is_rejected():
    payload = valid()
    payload["items"].append({**payload["items"][0], "kiz_codes": []})
    with pytest.raises(ValidationError):
        KizTransferRequest.model_validate(payload)
