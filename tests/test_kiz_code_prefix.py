import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.kiz_code import normalize_kiz_code, normalize_kiz_payload
from app.core.kiz_operation_idempotency import kiz_operation_fingerprint
from app.core.container_operation_idempotency import container_operation_fingerprint
from app.core.schemas.kiz import KizAssignment
from app.core.schemas.kiz_activation import KizActivationRequest
from app.core.schemas.kiz_operations import KizTransferItem, KizShipItem
from app.core.schemas.container_operations import ContainerFillItem, ContainerExtractItem
from app.core.schemas.write_off_fbs import KizBinding
from app.core.services.kiz_activation_service import KizActivationService
from app.core.services.kiz_operation_idempotency_service import KizOperationIdempotencyService
from app.core.services.container_operation_idempotency_service import (
    ContainerOperationIdempotencyService,
)
from app.core.exceptions import (
    KizConflictError,
    KizOperationIdempotencyConflictError,
    ContainerOperationIdempotencyConflictError,
)
from app.core.services.kiz_import_service import KizImportService
from app.core.exceptions import KizImportBusinessError


@pytest.mark.parametrize("length", [1, 23, 24, 25, 32, 94, 96, 300])
@pytest.mark.parametrize("symbol", ["A", "Ж", "😀"])
def test_literal_unicode_prefix(length, symbol):
    code = symbol * length
    assert normalize_kiz_code(code) == code[:24]
    assert KizBinding(assembly_task="1", kiz_code=code).kiz_code == code[:24]


@pytest.mark.parametrize("code", [" ", " code\t\n\r\x1d ", "\t" + "X" * 23 + "tail"])
def test_assignment_keeps_literal_spaces_and_controls(code):
    request = KizAssignment(
        kiz_code=code, product_id="testwild", location_code="LOC", author="tester"
    )
    assert request.kiz_code == code[:24]


@pytest.mark.parametrize(
    "model", [KizTransferItem, KizShipItem, ContainerFillItem, ContainerExtractItem]
)
def test_all_physical_request_codes_normalize_before_duplicate_checks(model):
    payload = dict(
        external_line_id="line", product_id="testwild", quantity=2, kiz_codes=["X" * 24 + "old"]
    )
    if model in (KizTransferItem, KizShipItem):
        payload["from_location_code"] = "SOURCE"
    if model is KizTransferItem:
        payload["to_location_code"] = "DESTINATION"
    assert model(**payload).kiz_codes == ["X" * 24]
    payload["kiz_codes"].append("X" * 24 + "different")
    with pytest.raises(ValidationError):
        model(**payload)


def test_activation_duplicate_prefixes_are_rejected():
    with pytest.raises(ValidationError):
        KizActivationRequest(
            kiz_codes=["X" * 24 + "a", "X" * 24 + "b"],
            location_code="LOC",
            user_name="tester",
            idempotency_key="key",
        )


def test_import_duplicate_prefixes_and_literal_characters():
    service = KizImportService(None)
    message = {
        "parse_status": "received",
        "raw_payload": {
            "supply": {"order_guid": "receipt"},
            "wild_groups": [{"wild": "testwild", "mark_codes": [" code\t\n\x1d "]}],
        },
    }
    assert service._canonicalize(message).codes_by_product == {"testwild": [" code\t\n\x1d "]}
    message["raw_payload"]["wild_groups"][0]["mark_codes"] = ["X" * 24 + "a", "X" * 24 + "b"]
    with pytest.raises(KizImportBusinessError) as error:
        service._canonicalize(message)
    assert error.value.error_code == "DUPLICATE_KIZ_CODE"


@pytest.mark.parametrize(
    "fingerprint", [kiz_operation_fingerprint, container_operation_fingerprint]
)
def test_fingerprint_uses_canonical_codes(fingerprint):
    left = {
        "operation_type": "ship",
        "items": [{"external_line_id": "1", "kiz_codes": ["X" * 24 + "one"]}],
    }
    right = normalize_kiz_payload(left)
    assert fingerprint(left) == fingerprint(right)


@pytest.mark.parametrize(
    "service_class,fingerprint,error_class",
    [
        (
            KizOperationIdempotencyService,
            kiz_operation_fingerprint,
            KizOperationIdempotencyConflictError,
        ),
        (
            ContainerOperationIdempotencyService,
            container_operation_fingerprint,
            ContainerOperationIdempotencyConflictError,
        ),
    ],
)
async def test_legacy_fingerprint_replay_and_changed_intent_conflict(
    service_class, fingerprint, error_class
):
    old_intent = {
        "operation_type": "ship",
        "items": [{"external_line_id": "1", "quantity": 1, "kiz_codes": ["X" * 24 + "original"]}],
    }
    result = {"items": old_intent["items"]}
    row = dict(
        operation_id=1,
        request_fingerprint=fingerprint(old_intent, normalize_codes=False),
        result_payload=result,
    )
    repository = AsyncMock()
    repository.try_create.return_value = None
    repository.get_for_update.return_value = row
    connection = AsyncMock()
    connection.is_in_transaction = lambda: True
    service = service_class(repository)

    async def acquire(intent):
        if service_class is KizOperationIdempotencyService:
            return await service.acquire(
                connection,
                operation_type="ship",
                source_system="test",
                external_operation_id="key",
                author="tester",
                intent=intent,
            )
        data = type(
            "Request",
            (),
            dict(
                container_id=1, source_system="test", external_operation_id="key", author="tester"
            ),
        )()
        return await service.acquire(connection, data=data, intent=intent)

    intent = normalize_kiz_payload(old_intent)
    replay = await acquire(intent)
    assert replay.operation["result_payload"]["items"][0]["kiz_codes"] == ["X" * 24]
    intent["items"][0]["quantity"] = 2
    with pytest.raises(error_class):
        await acquire(intent)


def test_legacy_activation_replay_preserves_original_fingerprint_validation():
    old_intent = {
        "kiz_codes": ["X" * 24 + "original"],
        "location_code": "LOC",
        "user_name": "tester",
    }
    operation = dict(
        request_fingerprint=hashlib.sha256(
            json.dumps(
                old_intent, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        result_payload={"items": [{"kiz_code": old_intent["kiz_codes"][0]}]},
    )
    request = KizActivationRequest(**old_intent, idempotency_key="key")
    result = KizActivationService._replay_operation(
        operation, KizActivationService._request_hash(request), request
    )
    assert result["items"][0]["kiz_code"] == "X" * 24
    request.user_name = "other"
    with pytest.raises(KizConflictError):
        KizActivationService._replay_operation(
            operation, KizActivationService._request_hash(request), request
        )
