"""OpenAPI text and example for the read-only KIZ history endpoint."""

HISTORY_EXAMPLE = {
    "kiz_code": "KIZ-001",
    "product_id": "wild1825",
    "current_state": {
        "kiz_id": 1,
        "kiz_code": "KIZ-001",
        "product_id": "wild1825",
        "location_id": None,
        "location_code": None,
        "lifecycle_status": "shipped",
        "origin_type": "warehouse_assignment",
        "origin_reference": None,
        "assigned_at": "2026-09-15T10:00:00+03:00",
        "closed_at": "2026-09-15T12:00:00+03:00",
        "created_at": "2026-09-15T10:00:00+03:00",
        "updated_at": "2026-09-15T12:00:00+03:00",
        "created_by": "operator",
        "metadata": {},
    },
    "timeline": [
        {
            "event_type": "assigned",
            "occurred_at": "2026-09-15T10:00:00+03:00",
            "location_code": "STORAGE-A-01",
            "movement_ref": None,
            "kiz_event_id": 101,
            "to_status": "active",
            "author": "operator",
        },
        {
            "event_type": "ship",
            "occurred_at": "2026-09-15T12:00:00+03:00",
            "from_location_code": "STORAGE-A-01",
            "to_location_code": None,
            "movement_ref": 15002,
            "quantity": "5.00",
            "kiz_event_id": 102,
            "from_status": "active",
            "to_status": "shipped",
            "author": "operator",
        },
    ],
}

OPERATION = {
    "summary": "Получить полную историю конкретного КИЗ",
    "description": """Возвращает текущую карточку и хронологическую timeline без нового ledger.

Lifecycle/identity события (`assigned`, `marked_as_error`, `deactivated`) читаются из
`wms.kiz_events`. Physical transfer/ship читаются только по цепочке
`wms.kiz_movement_links → wms.movement_registry → wms.movements`, без эвристик по
reason или metadata. `shipped` event с тем же `movement_ref`, что и linked ship
movement, объединяется в одну запись `ship`; lifecycle-поля при этом сохраняются.

Warehouse assignment не создаёт artificial receive movement. Поле `quantity` — полное
количество movement, а не количество конкретного КИЗ: linked KIZ представляет одну
identified unit внутри него. История shipped KIZ доступна при текущей location `null`.
Запрос read-only и не изменяет projection или журналы.""",
    "responses": {
        200: {
            "description": "Текущая карточка и единая хронология КИЗ.",
            "content": {"application/json": {"example": HISTORY_EXAMPLE}},
        },
        404: {
            "description": "КИЗ не найден.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "КИЗ KIZ-UNKNOWN не найден",
                        "error_code": "KIZ_NOT_FOUND",
                    }
                }
            },
        },
    },
}
