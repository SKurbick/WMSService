from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.core.schemas.kiz_operations import KizShipRequest


def valid():
    return {
        "source_system": "manual",
        "external_operation_id": "ship-1",
        "author": "operator",
        "items": [{
            "external_line_id": "1",
            "product_id": "testwild",
            "from_location_code": "SOURCE",
            "quantity": 2,
            "kiz_codes": ["K1", "K2"],
        }],
    }


@pytest.mark.parametrize("mutation", [
    lambda p: p["items"][0].update(quantity=0),
    lambda p: p["items"][0].update(quantity=-1),
    lambda p: p["items"][0].update(quantity="2"),
    lambda p: p["items"][0].update(quantity=2.0),
    lambda p: p["items"][0].update(quantity=1.5),
    lambda p: p["items"][0].update(quantity=1, kiz_codes=["K1", "K2"]),
    lambda p: p["items"][0].update(kiz_codes=["K1", "K1"]),
    lambda p: p.update(source_system="bad\x00value"),
    lambda p: p.update(author="\ud800"),
])
def test_invalid_ship_request_is_rejected(mutation):
    payload = valid()
    mutation(payload)
    with pytest.raises(ValidationError):
        KizShipRequest.model_validate(payload)


def test_duplicate_kiz_across_items_is_rejected():
    payload = valid()
    payload["items"].append({
        **deepcopy(payload["items"][0]),
        "external_line_id": "2",
        "kiz_codes": ["K1"],
        "quantity": 1,
    })
    with pytest.raises(ValidationError):
        KizShipRequest.model_validate(payload)


def test_duplicate_external_line_is_rejected():
    payload = valid()
    payload["items"].append({
        **deepcopy(payload["items"][0]),
        "kiz_codes": [],
    })
    with pytest.raises(ValidationError):
        KizShipRequest.model_validate(payload)


def test_kiz_and_item_order_are_accepted_as_semantically_unordered():
    one = KizShipRequest.model_validate(valid())
    payload = valid()
    payload["items"][0]["kiz_codes"].reverse()
    payload["items"].append({
        "external_line_id": "2",
        "product_id": "testwild2",
        "from_location_code": "OTHER",
        "quantity": 1,
        "kiz_codes": [],
    })
    two = KizShipRequest.model_validate(payload)
    assert one.items[0].quantity == 2
    assert two.items[0].kiz_codes == ["K2", "K1"]
