"""Только OpenAPI metadata для read-only API истории WMS."""

from typing import Any

from pydantic import BaseModel, Field


class HistoryErrorResponse(BaseModel):
    detail: str = Field(description="Пользовательское описание ошибки.")
    error_code: str = Field(description="Стабильный программный код ошибки.")
    message: str | None = Field(
        default=None, description="Дублирующее сообщение у validation handlers."
    )


class ValidationErrorResponse(BaseModel):
    detail: list[dict[str, Any]] = Field(description="Ошибки FastAPI/Pydantic validation.")


def error_response(description: str, example: dict, *, validation: bool = False):
    return {
        "model": ValidationErrorResponse if validation else HistoryErrorResponse,
        "description": description,
        "content": {"application/json": {"example": example}},
    }


DAILY_EXAMPLE = {
    "date_from": "2026-07-01",
    "date_to": "2026-07-03",
    "timezone": "Europe/Moscow",
    "location_id": None,
    "include_subtree": False,
    "total_products": 1,
    "limit": 100,
    "offset": 0,
    "items": [
        {
            "product_id": "wild1825",
            "product_name": "Товар",
            "days": [
                {
                    "date": "2026-07-01",
                    "opening_quantity": 0,
                    "incoming_quantity": 10,
                    "outgoing_quantity": 0,
                    "closing_quantity": 10,
                },
                {
                    "date": "2026-07-02",
                    "opening_quantity": 10,
                    "incoming_quantity": 0,
                    "outgoing_quantity": 0,
                    "closing_quantity": 10,
                },
                {
                    "date": "2026-07-03",
                    "opening_quantity": 10,
                    "incoming_quantity": 0,
                    "outgoing_quantity": 4,
                    "closing_quantity": 6,
                },
            ],
        }
    ],
}


def operation_list_example(source_type, event_id, operation_type, name, status="completed"):
    return {
        "date_from": "2026-07-01",
        "date_to": "2026-07-31",
        "timezone": "Europe/Moscow",
        "total": 1,
        "limit": 100,
        "offset": 0,
        "items": [
            {
                "event_id": event_id,
                "source_type": source_type,
                "operation_type": operation_type,
                "operation_name": name,
                "status": status,
                "created_at": "2026-07-22T07:15:00+00:00",
                "completed_at": "2026-07-22T07:15:01+00:00" if status else None,
                "author": "operator",
                "location_id": 123 if source_type != "fbs_shipment" else None,
                "location_code": "PUSHKINO-УПАКОВКА" if source_type != "fbs_shipment" else None,
                "product_count": 1,
                "total_quantity": 1,
                "external_reference": None,
            }
        ],
    }


OPERATIONS_EXAMPLES = {
    "kit": {
        "summary": "Комплектация",
        "value": operation_list_example(
            "kit_operation", "kit_operation:42", "kit_assembly", "Комплектация"
        ),
    },
    "re_sorting": {
        "summary": "Пересортица",
        "value": operation_list_example(
            "re_sorting_operation", "re_sorting:7", "re_sorting", "Пересортица"
        ),
    },
    "fbs": {
        "summary": "FBS-отгрузка",
        "value": operation_list_example(
            "fbs_shipment", "fbs_shipment:156", "fbs_shipment", "ФБС-отгрузка", "failed"
        ),
    },
    "movement": {
        "summary": "Самостоятельное движение",
        "value": operation_list_example(
            "movement", "movement:29530:1784198400451000", "transfer", "Перемещение", None
        ),
    },
}


MOVEMENT = {
    "event_id": "movement:29530:1784198400451000",
    "movement_id": 29530,
    "movement_type": "transfer",
    "product_id": "wild1825",
    "product_name": "Товар",
    "quantity": 4,
    "from_location_id": 10,
    "from_location_code": "A-01",
    "to_location_id": 20,
    "to_location_code": "B-01",
    "batch_number": None,
    "container_code": None,
    "user_name": "operator",
    "reason": "Перемещение",
    "source_type": None,
    "source_id": None,
    "source_item_id": None,
    "metadata": {},
    "created_at": "2026-07-16T07:40:00.451000+00:00",
}

DETAIL_EXAMPLES = {
    "kit": {
        "summary": "Комплектация",
        "value": {
            "event_id": "kit_operation:42",
            "source_type": "kit_operation",
            "operation_type": "kit_assembly",
            "operation_name": "Комплектация",
            "status": "completed",
            "created_at": "2026-07-22T07:15:00+00:00",
            "completed_at": "2026-07-22T07:15:01+00:00",
            "author": "operator",
            "header": {
                "operation_id": 42,
                "operation_type": "assembly",
                "kit_product_id": "kit-1",
                "kit_product_name": "Комплект",
                "quantity": 1,
                "operation_location_id": 1,
                "location_id": 123,
                "location_code": "PACK",
                "author": "operator",
                "status": "completed",
                "created_at": "2026-07-22T07:15:00+00:00",
                "completed_at": "2026-07-22T07:15:01+00:00",
            },
            "items": [],
            "movements": [],
            "warnings": [],
        },
    },
    "re_sorting": {
        "summary": "Пересортица",
        "value": {
            "event_id": "re_sorting:7",
            "source_type": "re_sorting_operation",
            "operation_type": "re_sorting",
            "operation_name": "Пересортица",
            "status": "completed",
            "created_at": "2026-07-22T07:15:00+00:00",
            "completed_at": "2026-07-22T07:15:01+00:00",
            "author": "operator",
            "header": {
                "operation_id": 7,
                "from_product_id": "sku-a",
                "from_product_name": "A",
                "to_product_id": "sku-b",
                "to_product_name": "B",
                "quantity": 4,
                "operation_location_id": 1,
                "location_id": 123,
                "location_code": "PACK",
                "reason": "Пересортица",
                "author": "operator",
                "status": "completed",
                "created_at": "2026-07-22T07:15:00+00:00",
                "completed_at": "2026-07-22T07:15:01+00:00",
            },
            "items": [],
            "movements": [],
            "warnings": [],
        },
    },
    "fbs": {
        "summary": "FBS shipment с warning",
        "value": {
            "event_id": "fbs_shipment:156",
            "source_type": "fbs_shipment",
            "operation_type": "fbs_shipment",
            "operation_name": "ФБС-отгрузка",
            "status": "failed",
            "created_at": "2026-07-22T07:15:00+00:00",
            "completed_at": None,
            "author": None,
            "header": {
                "shipment_id": 156,
                "source": "rabbitmq",
                "status": "failed",
                "received_at": "2026-07-22T07:15:00+00:00",
                "completed_at": None,
                "total_items": 0,
                "error_message": "Movement missing",
                "raw_message": {},
            },
            "items": [],
            "movements": [],
            "warnings": [
                {
                    "code": "missing_movement_link",
                    "message": "Movement 123 was not found",
                    "reference": "123",
                }
            ],
        },
    },
    "movement": {
        "summary": "Standalone transfer",
        "value": {
            "event_id": MOVEMENT["event_id"],
            "source_type": "movement",
            "operation_type": "transfer",
            "operation_name": "Перемещение",
            "status": None,
            "created_at": MOVEMENT["created_at"],
            "completed_at": None,
            "author": "operator",
            "header": MOVEMENT,
            "items": [],
            "movements": [MOVEMENT],
            "warnings": [],
        },
    },
}


SNAPSHOT = {
    "revision_at": "2026-07-22T09:30:00+00:00",
    "event_status": "Проведён",
    "document_created_at": "2026-07-20T07:00:00+00:00",
    "supply_date": "2026-07-21T07:00:00+00:00",
    "update_document_datetime": "2026-07-22T09:30:00+00:00",
    "supplier_name": "Поставщик",
    "supplier_code": "SUP-1",
    "author_of_the_change": "Иванов",
    "our_organizations_name": "Организация",
    "order_guid": "order-guid",
    "currency": "RUB",
    "items": [
        {
            "receipt_item_id": 501,
            "product_id": "wild1825",
            "product_name": "Товар",
            "quantity": 10,
            "created_at": "2026-07-22T09:30:01+00:00",
            "updated_at": "2026-07-22T09:30:01+00:00",
        }
    ],
}
REVISION = {
    "revision_id": "receipt_revision:1784712600000000",
    "revision_at": "2026-07-22T09:30:00+00:00",
    "is_current": True,
    "document_number": "ПТУ-123",
    "document_created_at": "2026-07-20T07:00:00+00:00",
    "supply_date": "2026-07-21T07:00:00+00:00",
    "update_document_datetime": "2026-07-22T09:30:00+00:00",
    "event_status": "Проведён",
    "supplier_name": "Поставщик",
    "supplier_code": "SUP-1",
    "author_of_the_change": "Иванов",
    "our_organizations_name": "Организация",
    "order_guid": "order-guid",
    "currency": "RUB",
    "invoice_number": None,
    "transport_number": None,
    "items": [
        {
            "legacy_row_id": 100,
            "product_id": "wild1825",
            "product_name": "Товар",
            "quantity": 10,
            "amount_with_vat": 1000,
            "amount_without_vat": 833.33,
            "planned_cost": 900,
            "pack_count": None,
            "pack_multiplicity": None,
            "is_valid": True,
        }
    ],
}
RECEIPT_EXAMPLES = {
    "both": {
        "summary": "История и текущий snapshot",
        "value": {
            "guid": "document-guid",
            "document_number": "ПТУ-123",
            "total_revisions": 1,
            "limit": 50,
            "offset": 0,
            "current_snapshot": SNAPSHOT,
            "revisions": [REVISION],
        },
    },
    "legacy_only": {
        "summary": "Только legacy history",
        "value": {
            "guid": "document-guid",
            "document_number": "ПТУ-123",
            "total_revisions": 1,
            "limit": 50,
            "offset": 0,
            "current_snapshot": None,
            "revisions": [REVISION],
        },
    },
    "snapshot_only": {
        "summary": "Только WMS snapshot",
        "value": {
            "guid": "document-guid",
            "document_number": "ПТУ-123",
            "total_revisions": 0,
            "limit": 50,
            "offset": 0,
            "current_snapshot": SNAPSHOT,
            "revisions": [],
        },
    },
}

RECEIPT_LIST_LEGACY_ITEM = {
    "row_id": "receipt_revision:ZG9jdW1lbnQtZ3VpZA:1784712600000000",
    "source_type": "legacy_revision",
    "guid": "document-guid",
    "revision_id": "receipt_revision:1784712600000000",
    "revision_at": "2026-07-22T09:30:00+00:00",
    "is_current": True,
    "has_current_snapshot": True,
    "snapshot_updated_at": "2026-07-22T09:30:01+00:00",
    "document_number": "ПТУ-123",
    "document_created_at": "2026-07-20T07:00:00+00:00",
    "supply_date": "2026-07-21T07:00:00+00:00",
    "update_document_datetime": "2026-07-22T09:30:00+00:00",
    "event_status": "Проведён",
    "supplier_name": "Поставщик",
    "supplier_code": "SUP-1",
    "author_of_the_change": "Иванов",
    "our_organizations_name": "Организация",
    "order_guid": "order-guid",
    "currency": "RUB",
    "invoice_number": "INV-123",
    "transport_number": "TR-55",
    "item_count": 2,
    "product_count": 2,
    "total_quantity": 16,
}
RECEIPT_LIST_WMS_ITEM = {
    **RECEIPT_LIST_LEGACY_ITEM,
    "row_id": "receipt_snapshot:d21zLW9ubHk",
    "source_type": "wms_snapshot_only",
    "guid": "wms-only",
    "revision_id": None,
    "invoice_number": None,
    "transport_number": None,
    "item_count": 1,
    "product_count": 1,
    "total_quantity": 5,
}


def receipt_list_response(items):
    return {
        "date_from": "2026-07-01",
        "date_to": "2026-07-31",
        "timezone": "Europe/Moscow",
        "total": len(items),
        "total_documents": len({item["guid"] for item in items}),
        "limit": 50,
        "offset": 0,
        "items": items,
    }


RECEIPT_LIST_EXAMPLES = {
    "two_revisions": {
        "summary": "Две ревизии одного документа",
        "value": receipt_list_response(
            [
                {**RECEIPT_LIST_LEGACY_ITEM, "is_current": True},
                {
                    **RECEIPT_LIST_LEGACY_ITEM,
                    "row_id": "receipt_revision:ZG9jdW1lbnQtZ3VpZA:1784626200000000",
                    "revision_id": "receipt_revision:1784626200000000",
                    "revision_at": "2026-07-21T09:30:00+00:00",
                    "is_current": False,
                },
            ]
        ),
    },
    "documents": {
        "summary": "Несколько документов",
        "value": receipt_list_response(
            [
                RECEIPT_LIST_LEGACY_ITEM,
                {
                    **RECEIPT_LIST_LEGACY_ITEM,
                    "guid": "document-2",
                    "row_id": "receipt_revision:ZG9jdW1lbnQtMg:1784712600000000",
                },
            ]
        ),
    },
    "wms_only": {
        "summary": "WMS-only snapshot",
        "value": receipt_list_response([RECEIPT_LIST_WMS_ITEM]),
    },
    "empty": {"summary": "Пустой результат", "value": receipt_list_response([])},
    "current": {
        "summary": "Фильтр is_current=true",
        "value": receipt_list_response([RECEIPT_LIST_LEGACY_ITEM]),
    },
}
