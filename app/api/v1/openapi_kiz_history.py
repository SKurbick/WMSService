"""Русское описание и пример единой истории КИЗ."""

from app.api.v1.openapi_kiz import SERVER_ERROR_RESPONSE, VALIDATION_RESPONSE

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
    "description": """Возвращает текущую карточку current_state и полную хронологию timeline
из событий жизненного цикла и связанных физических движений. Код передаётся query-параметром,
поэтому для кодов со слешами этот маршрут удобнее карточки с кодом в URL. Регистр значим.

События assigned, marked_as_error, deactivated читаются из журнала КИЗ. Движения
находятся только по явным связям КИЗ с movement_ref. Событие shipped объединяется
со связанным движением ship в одну запись с сохранением полей жизненного цикла.
Операции контейнера объединяют физические части в одно событие: movement_refs
содержит все связанные ссылки, container_* — исторические сведения о контейнере.

Назначение КИЗ не создаёт приходного движения. quantity — полное количество
связанного движения, а конкретный КИЗ представляет одну единицу внутри него.
История отгруженного КИЗ доступна и при текущей location_id=null.
Ответ читается в одном согласованном снимке, без пагинации и изменения данных.
Нарушенная связь с физическим движением вызывает ошибку сервера, а не выдуманную запись.""",
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
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
}
