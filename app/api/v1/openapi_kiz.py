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
    "container_id": None,
    "container_qr_code": None,
    "container_location_id": None,
    "container_location_code": None,
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
Для дальнейшего перемещения используйте `/api/kiz-operations/transfer`, для отгрузки —
`/api/kiz-operations/ship`. Назначение создаёт active КИЗ непосредственно на россыпь;
назначение сразу в контейнер этим запросом не поддерживается. Поле author записывает
автора операции, но само по себе не подтверждает права доступа.""",
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
        "description": """Для существующего товара укажите ровно один способ выбора места хранения:
`location_code` — россыпь на точном адресе, `container_id` либо `container_qr_code` —
содержимое конкретного контейнера. Отсутствие или сочетание нескольких способов даёт 409.
Дочерние адреса и партии не учитываются; используется только остаток available.

Возвращает physical_quantity, количество active КИЗ identified_quantity и разницу
unidentified_quantity. Для контейнера location_id/location_code равны null, а его
текущий адрес указан в container_location_id/container_location_code. Если строки
остатка нет, physical_quantity=0. При несогласованности unidentified_quantity может
быть отрицательным, integrity_ok=false; запрос ничего не исправляет.

Неизвестный товар или адрес — 404, неизвестный контейнер — 409 KIZ_CONFLICT.
Мягкие резервы в расчёт не входят. Десятичные количества возвращаются строками.""",
        "responses": {200: success(SUMMARY)},
    },
    "list": {
        "summary": "Получить список КИЗ",
        "description": """Фильтры по товару, точному коду локации и состоянию можно комбинировать.
Можно фильтровать по container_id или container_qr_code. location_code фильтрует только
прямую локацию КИЗ, а не адрес контейнера: сочетание location_code с фильтром контейнера
не выбирает contained КИЗ по адресу контейнера. Без фильтра состояния возвращаются
active, error, deactivated и shipped. Сортировка — по kiz_id по возрастанию.
Количество total и страница items читаются из одного снимка данных. Пустой результат — 200 с items=[].""",
        "responses": {200: success({"items": [STATE], "total": 1, "limit": 50, "offset": 0})},
    },
    "events": {
        "summary": "Получить журнал событий жизненного цикла КИЗ",
        "description": """Неизменяемый журнал событий assigned, marked_as_error, deactivated и shipped.
Сортировка — от ранних событий к поздним по occurred_at, затем kiz_event_id.
Метаданные и причина закрытия доступны в соответствующем событии. Неизвестный код — 404.
Ответ содержит items, total, limit, offset; limit от 1 до 200, по умолчанию 50.
Перемещения и операции с контейнерами смотрите в единой истории `/api/kiz-history`.
Чтение журнала не меняет состояние КИЗ и остатки.""",
        "responses": {200: success({"items": [EVENT], "total": 1, "limit": 50, "offset": 0})},
    },
    "error": {
        "summary": "Отметить ошибочное назначение КИЗ",
        "description": """Переход active → error с обязательными автором и причиной. Карточка и событие
marked_as_error изменяются атомарно. Физический остаток не меняется; число активных КИЗ уменьшается.
Код и его история сохраняются. Поддерживаются active КИЗ на россыпи и в контейнере;
место хранения сохраняется в карточке и событии. Повторное закрытие и возврат в active
запрещены (409). Ошибка откатывает весь переход и событие.""",
        "responses": {200: success(closed_state("error")), 409: CONFLICT_RESPONSE},
    },
    "deactivate": {
        "summary": "Деактивировать КИЗ",
        "description": """Окончательный переход active → deactivated с обязательными автором и причиной.
Сохраняет событие deactivated. Физический остаток не меняется, движение не создаётся.
Это не отгрузка товара. Поддерживаются КИЗ на россыпи и в контейнере; место хранения
сохраняется. Код нельзя повторно назначить или активировать. Повторное закрытие — 409.
Автор и непустая причина обязательны; ошибка откатывает весь переход и событие.""",
        "responses": {200: success(closed_state("deactivated")), 409: CONFLICT_RESPONSE},
    },
    "get": {
        "summary": "Получить карточку КИЗ",
        "description": """Возвращает текущее состояние активного или закрытого КИЗ вместе с исходными
метаданными назначения. Для active КИЗ заполнен один прямой holder: location_id либо
container_id. Адрес контейнера возвращается отдельно в container_location_*.
У shipped КИЗ прямые location_id и container_id равны null, но карточка остаётся доступной.
Для журнала lifecycle используйте /events, для полной истории с движениями — /api/kiz-history.
Код в URL передавайте с percent-encoding; регистр значим. Статические маршруты имеют
приоритет: код stock-summary и коды, оканчивающиеся на /events, могут пересекаться с ними.""",
        "responses": {200: success(STATE)},
    },
}


VALIDATION_RESPONSE = {
    "description": "Ошибка формата запроса или параметров; detail содержит список ошибок валидации.",
}
SERVER_ERROR_RESPONSE = {
    "description": "Внутренняя ошибка сервера или несогласованность данных. Причина возвращается в detail.",
}
OPERATION_CONFLICT_RESPONSE = {
    "description": (
        "Недостаточно физического или неидентифицированного остатка, недопустимый КИЗ: "
        "KIZ_CONFLICT. Конкурентная запись: CONCURRENT_WRITE_CONFLICT — повторить весь запрос "
        "с тем же ключом. Тот же ключ для другой команды: KIZ_IDEMPOTENCY_CONFLICT, "
        "в ответе также возвращается operation_id."
    ),
    "content": {
        "application/json": {
            "examples": {
                "stock": {
                    "summary": "Недостаток остатка",
                    "value": {
                        "detail": "Недостаточно неидентифицированного остатка",
                        "error_code": "KIZ_CONFLICT",
                    },
                },
                "idempotency": {
                    "summary": "Ключ использован для другой команды",
                    "value": {
                        "detail": "Ключ уже зарегистрирован для другого набора действий",
                        "error_code": "KIZ_IDEMPOTENCY_CONFLICT",
                        "operation_id": 15001,
                    },
                },
            }
        }
    },
}

for operation in OPERATIONS.values():
    operation["responses"].setdefault(422, VALIDATION_RESPONSE)
    operation["responses"].setdefault(500, SERVER_ERROR_RESPONSE)


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
