"""Русские описания и примеры OpenAPI; не участвуют в выполнении операций."""

ASSIGNMENT = {
    "kiz_code": "KIZ-001",
    "product_id": "wild1825",
    "location_code": "STORAGE-A-01",
    "author": "operator",
    "metadata": {"comment": "Назначение после сканирования"},
}
TERMINAL = {
    "author": "operator",
    "reason": "Код ошибочно назначен этому товару",
    "metadata": {"ticket": "WMS-123"},
}
STATE = {
    "kiz_id": 1,
    "kiz_code": "KIZ-001",
    "product_id": "wild1825",
    "location_id": 10,
    "location_code": "STORAGE-A-01",
    "lifecycle_status": "active",
    "origin_type": "warehouse_assignment",
    "origin_reference": None,
    "assigned_at": "2026-09-07T10:00:00+03:00",
    "closed_at": None,
    "created_at": "2026-09-07T10:00:00+03:00",
    "updated_at": "2026-09-07T10:00:00+03:00",
    "created_by": "operator",
    "metadata": ASSIGNMENT["metadata"],
}
SUMMARY = {
    "product_id": "wild1825",
    "location_id": 10,
    "location_code": "STORAGE-A-01",
    "physical_quantity": "10.00",
    "identified_quantity": 1,
    "unidentified_quantity": "9.00",
    "integrity_ok": True,
}
EVENT = {
    "kiz_event_id": 1,
    "kiz_id": 1,
    "event_type": "assigned",
    "from_status": None,
    "to_status": "active",
    "product_id": "wild1825",
    "location_id": 10,
    "author": "operator",
    "reason": None,
    "metadata": ASSIGNMENT["metadata"],
    "occurred_at": "2026-09-07T10:00:00+03:00",
}
INTEGRITY_EXAMPLE = {
    "product_id": "wild1825",
    "location_id": 10,
    "location_code": "STORAGE-A-01",
    "physical_quantity": "3.00",
    "identified_quantity": 4,
    "difference": "1.00",
    "inventory_missing": False,
}

CONFLICT_RESPONSE = {
    "description": "Конфликт КИЗ или конкурентного изменения. Причина в detail, тип в error_code.",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["detail", "error_code"],
                "properties": {
                    "detail": {
                        "type": "string",
                        "description": "Описание причины на русском языке.",
                    },
                    "error_code": {"type": "string", "description": "Программный код конфликта."},
                    "diagnostics": {
                        "description": "Дополнительные сведения о нарушенных остатках, если доступны.",
                        "oneOf": [
                            {"type": "object"},
                            {"type": "array", "items": {"type": "object"}},
                        ],
                    },
                },
            },
            "examples": {
                "kiz": {
                    "summary": "Расход затрагивает активные КИЗ",
                    "value": {
                        "detail": "Недостаточно неидентифицированного остатка: physical=10, identified=4, requested_remaining=3",
                        "error_code": "KIZ_CONFLICT",
                        "diagnostics": {
                            "product_id": "wild1825",
                            "location_id": 10,
                            "physical_quantity": 10,
                            "identified_quantity": 4,
                            "requested_remaining": 3,
                        },
                    },
                },
                "concurrent": {
                    "summary": "Повторить операцию целиком после конкурентного конфликта",
                    "value": {
                        "detail": "Конкурентное изменение остатка. Повторите операцию целиком.",
                        "error_code": "CONCURRENT_WRITE_CONFLICT",
                    },
                },
            },
        }
    },
}


def success(example, description="Операция выполнена успешно."):
    return {"description": description, "content": {"application/json": {"example": example}}}


def closed_state(status):
    return {
        **STATE,
        "lifecycle_status": status,
        "closed_at": "2026-09-07T11:00:00+03:00",
        "updated_at": "2026-09-07T11:00:00+03:00",
    }


OPERATIONS = {
    "assign": {
        "summary": "Назначить КИЗ существующей единице товара",
        "description": """Идентифицирует одну единицу уже существующего остатка. Товар и точная локация должны существовать.
Учитывается только `available` без партии и контейнера. Нужна минимум одна целая
неидентифицированная единица: при остатке 1.5 можно назначить один КИЗ.

Количество товара не меняется, движение не создаётся. Карточка и событие `assigned`
сохраняются атомарно. Служебное время обновления остатка может измениться.
Код уникален навсегда: повтор того же запроса, включая уже закрытый код, возвращает 409.

После назначения обычный расход не может затронуть идентифицированную часть остатка.
Физическая отгрузка и перемещение конкретных КИЗ в первой версии не реализованы.""",
        "responses": {
            201: success(
                {**SUMMARY, "kiz": STATE},
                "КИЗ назначен; возвращены карточка и остатки после назначения.",
            ),
            409: CONFLICT_RESPONSE,
        },
    },
    "summary": {
        "summary": "Получить физический и идентифицированный остаток",
        "description": """Только чтение. Для товара и точного адреса возвращает физическое количество,
число активных КИЗ и разницу между ними. Дочерние адреса, партии и контейнеры не учитываются.
Если строка остатка отсутствует, physical_quantity равен 0. Неизвестный товар или адрес — 404.
Это не расчёт свободного остатка после мягких резервов. Десятичные количества возвращаются строками.""",
        "responses": {200: success(SUMMARY)},
    },
    "list": {
        "summary": "Получить список КИЗ",
        "description": """Фильтры по товару, точному коду локации и состоянию можно комбинировать.
Без фильтра состояния возвращаются активные и закрытые КИЗ. Сортировка — по kiz_id по возрастанию.
Количество total и страница items читаются из одного снимка данных. Пустой результат — 200 с items=[].""",
        "responses": {200: success({"items": [STATE], "total": 1, "limit": 50, "offset": 0})},
    },
    "events": {
        "summary": "Получить историю КИЗ",
        "description": """Неизменяемый журнал назначения и закрытия конкретного КИЗ.
Сортировка — от ранних событий к поздним по occurred_at, затем kiz_event_id.
Метаданные и причина закрытия доступны в соответствующем событии. Неизвестный код — 404.""",
        "responses": {200: success({"items": [EVENT], "total": 1, "limit": 50, "offset": 0})},
    },
    "error": {
        "summary": "Отметить ошибочное назначение КИЗ",
        "description": """Переход active → error с обязательными автором и причиной. Карточка и событие
marked_as_error изменяются атомарно. Физический остаток не меняется; число активных КИЗ уменьшается.
Код и его история сохраняются. Повторное закрытие и возврат в active запрещены (409).""",
        "responses": {200: success(closed_state("error")), 409: CONFLICT_RESPONSE},
    },
    "deactivate": {
        "summary": "Деактивировать КИЗ",
        "description": """Окончательный переход active → deactivated с обязательными автором и причиной.
Сохраняет событие deactivated. Физический остаток не меняется, движение не создаётся.
Это не отгрузка товара. Код нельзя повторно назначить или активировать. Повторное закрытие — 409.""",
        "responses": {200: success(closed_state("deactivated")), 409: CONFLICT_RESPONSE},
    },
    "get": {
        "summary": "Получить карточку КИЗ",
        "description": """Возвращает текущее состояние активного или закрытого КИЗ вместе с исходными
метаданными назначения. Для истории используйте отдельный маршрут /events.
Код в URL передавайте с percent-encoding; регистр значим. Статические маршруты имеют
приоритет: код stock-summary и коды, оканчивающиеся на /events, могут пересекаться с ними.""",
        "responses": {200: success(STATE)},
    },
}


def preserve_response_examples(app):
    """Сохранить явные null в примерах: генератор FastAPI удаляет их с exclude_none."""
    from copy import deepcopy
    from fastapi.routing import APIRoute

    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        for route in app.routes:
            if not isinstance(route, APIRoute) or not route.include_in_schema:
                continue
            for method in route.methods:
                operation = schema["paths"].get(route.path_format, {}).get(method.lower())
                if operation is None:
                    continue
                for status, response in route.responses.items():
                    for media_type, media in response.get("content", {}).items():
                        target = operation["responses"][str(status)]["content"][media_type]
                        for key in ("example", "examples"):
                            if key in media:
                                target[key] = deepcopy(media[key])
        return schema

    app.openapi = openapi
