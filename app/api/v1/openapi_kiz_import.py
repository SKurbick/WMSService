"""Русские описания и примеры ответов API регистрации и активации КИЗ."""


ACTIVATION_EXAMPLE = {
    "operation_id": 42,
    "location_code": "PUSHKINO-ПРИЁМКА",
    "activated_count": 2,
    "already_active_count": 0,
    "items": [
        {
            "kiz_code": code,
            "product_id": "testwild",
            "lifecycle_status": "active",
            "location_code": "PUSHKINO-ПРИЁМКА",
        }
        for code in ["B21-KIZ-1", "B21-KIZ-2"]
    ],
}

MESSAGE_EXAMPLE = {
    "message_id": 123,
    "received_at": "2026-10-19T09:00:00Z",
    "parse_status": "received",
    "order_guid": "test-receipt-001",
    "supply_number": "TEST-SUPPLY-001",
    "organization": "Вектор",
    "exchange_name": "",
    "routing_key": "orders.kiz.imported",
    "wild_group_count": 1,
    "mark_code_count": 2,
    "business_status": "pending",
    "processed_at": None,
    "business_error_code": None,
    "business_error_message": None,
    "business_result": None,
}

RAW_PAYLOAD_EXAMPLE = {
    "supply": {
        "order_guid": "test-receipt-001",
        "supply_number": "TEST-SUPPLY-001",
        "organization": "Вектор",
    },
    "wild_groups": [{"wild": "testwild", "mark_codes": ["B21-KIZ-1", "B21-KIZ-2"]}],
}

MESSAGE_DETAIL_EXAMPLE = {
    **MESSAGE_EXAMPLE,
    "raw_body": (
        '{"supply":{"order_guid":"test-receipt-001",'
        '"supply_number":"TEST-SUPPLY-001","organization":"Вектор"},'
        '"wild_groups":[{"wild":"testwild","mark_codes":["B21-KIZ-1","B21-KIZ-2"]}]}'
    ),
    "raw_payload": RAW_PAYLOAD_EXAMPLE,
    "parse_error": None,
    "rabbit_message_id": "producer-message-001",
    "correlation_id": None,
    "headers": {},
    "created_at": "2026-10-19T09:00:00Z",
}

PROCESS_EXAMPLE = {
    "message_id": 123,
    "business_status": "applied",
    "order_guid": "test-receipt-001",
    "supply_number": "TEST-SUPPLY-001",
    "organization": "Вектор",
    "new_kiz_count": 2,
    "existing_kiz_count": 0,
    "terminal_existing_count": 0,
    "groups": [
        {
            "product_id": "testwild",
            "receipt_quantity": "3",
            "requested_codes": 2,
            "new_kiz": 2,
            "existing_kiz": 0,
            "terminal_existing": 0,
            "physical_quantity": "3",
            "identified_quantity": 0,
            "unidentified_quantity": "3",
        }
    ],
}

INTEGRITY_EXAMPLE = {
    "is_valid": True,
    "receipt_capacity_violations": [],
    "orphan_receipt_kiz": [],
    "registered_holder_violations": [],
    "registered_origin_violations": [],
    "invalid_message_kiz_links": [],
}

VALIDATION_RESPONSE = {
    "description": "Ошибка формата запроса или параметров. detail содержит список ошибок валидации.",
    "content": {
        "application/json": {
            "example": {
                "detail": [
                    {
                        "type": "greater_than_equal",
                        "loc": ["path", "message_id"],
                        "msg": "Input should be greater than or equal to 1",
                        "input": 0,
                        "ctx": {"ge": 1},
                    }
                ]
            }
        }
    },
}


def error_response(description: str, detail: str, error_code: str | None = None) -> dict:
    example = {"detail": detail}
    if error_code is not None:
        example["error_code"] = error_code
    return {
        "description": description,
        "content": {"application/json": {"example": example}},
    }


SERVER_ERROR_RESPONSE = {
    "description": "Внутренняя ошибка сервера или БД; операция не завершена успешно.",
    "content": {
        "application/json": {
            "example": {
                "detail": "Внутренняя ошибка сервера",
                "message": "Внутренняя ошибка сервера",
                "error_code": "INTERNAL_ERROR",
            }
        }
    },
}

ACTIVATION_DOCUMENTATION = {
    "summary": "Активировать зарегистрированные КИЗ в локации хранения россыпи",
    "description": (
        "Коды произвольной длины ищутся буквально по первым 24 символам; короткие коды "
        "используются целиком, пробелы и регистр не изменяются. "
        "Подтверждает физическое сканирование существующих КИЗ: переводит `registered` "
        "в `active`, назначает локацию и записывает событие `activated`. Товар берётся из КИЗ. "
        "Локация должна существовать и быть активной. Учитывается только доступная россыпь "
        "непосредственно в этой локации: без партии, контейнера и дочерних адресов.\n\n"
        "Для каждого товара новых активаций должно быть не больше неидентифицированного остатка "
        "(`physical - identified`). Физическое количество не меняется, движения не создаются: "
        "после активации identified увеличивается, unidentified уменьшается. Весь запрос атомарен; "
        "ошибка любого КИЗ откатывает все изменения. Активация в контейнере не поддерживается.\n\n"
        "Уже active КИЗ в той же локации учитывается в `already_active_count` без нового события. "
        "КИЗ в другой локации, в контейнере или в конечном состоянии вызывает конфликт. "
        "Повтор `idempotency_key` с теми же кодами, локацией и оператором возвращает сохранённый "
        "результат и `operation_id`; порядок кодов не важен. Изменение этих данных при том же ключе "
        "возвращает 409. С новым ключом уже active КИЗ в той же локации возвращается как уже активный."
    ),
    "responses": {
        200: {
            "description": "Все КИЗ активированы или уже активны в указанной локации; повтор возвращает сохранённый результат.",
            "content": {"application/json": {"example": ACTIVATION_EXAMPLE}},
        },
        404: error_response(
            "КИЗ или локация не найдены. error_code: KIZ_NOT_FOUND либо LOCATION_NOT_FOUND.",
            "КИЗ не найдены: B21-KIZ-1",
            "KIZ_NOT_FOUND",
        ),
        409: error_response(
            "Недостаточно свободного остатка, недопустимое состояние КИЗ/локации, конкурентное изменение или конфликт ключа повтора.",
            "Недостаточно неидентифицированного остатка для testwild",
            "KIZ_CONFLICT",
        ),
        422: {
            "description": "Ошибка запроса: пустые значения, дубликаты первых 24 символов КИЗ, недопустимые служебные поля или лишние поля. detail содержит список ошибок.",
        },
        500: SERVER_ERROR_RESPONSE,
    },
}

INTEGRITY_DOCUMENTATION = {
    "summary": "Проверить целостность регистрации КИЗ поступлений",
    "description": (
        "Проверяет превышение количества registered/active КИЗ над количеством поступления, "
        "КИЗ без строки поступления, недопустимое место хранения и происхождение registered КИЗ, "
        "а также несогласованные связи сообщение–КИЗ, включая разные заполненные организации. "
        "Читает данные без исправлений. Возвращает HTTP 200 даже при нарушениях: проверяйте "
        "`is_valid` и пять списков. Отсутствие receipt и превышение его количества информационны "
        "и не влияют на is_valid. Разные GUID сообщений одного КИЗ допустимы. Registered КИЗ без локации и контейнера — нормальное "
        "состояние. Эта проверка не заменяет общую сверку физических остатков WMS."
    ),
    "responses": {
        200: {
            "description": "Проверка выполнена; пустые списки нарушений соответствуют is_valid=true.",
            "content": {"application/json": {"example": INTEGRITY_EXAMPLE}},
        },
        500: SERVER_ERROR_RESPONSE,
    },
}

LIST_DOCUMENTATION = {
    "summary": "Получить список сообщений импорта КИЗ",
    "description": (
        "Возвращает диагностические поля сохранённых сообщений в порядке `message_id DESC` "
        "с пагинацией limit/offset. Ответ — массив без общего количества; пустой массив означает "
        "отсутствие записей на этой странице. Исходное тело и заголовки доступны в карточке "
        "сообщения. В список входят сообщения с ошибками разбора и отклонённые при регистрации. "
        "Каждая доставка RabbitMQ сохраняется отдельно, поэтому повторные доставки могут иметь "
        "разные message_id. Чтение списка не запускает регистрацию КИЗ."
    ),
    "responses": {
        200: {
            "description": "Страница журнала сообщений без исходного тела.",
            "content": {"application/json": {"example": [MESSAGE_EXAMPLE]}},
        },
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
}

DETAIL_DOCUMENTATION = {
    "summary": "Получить исходное сообщение импорта КИЗ и результат обработки",
    "description": (
        "Возвращает карточку сохранённой доставки: исходное тело `raw_body`, разобранный "
        "`raw_payload`, ошибки разбора, свойства RabbitMQ, статусы и сохранённый результат "
        "регистрации. Исходные поля сообщения не переписываются; диагностический order_guid "
        "может быть извлечён из supply_guid. Для тела вне UTF-8 сохраняется представление base64. "
        "Чтение карточки не применяет сообщение и не меняет состояние КИЗ или остатки."
    ),
    "responses": {
        200: {
            "description": "Карточка сообщения с исходным содержимым и диагностикой.",
            "content": {"application/json": {"example": MESSAGE_DETAIL_EXAMPLE}},
        },
        404: error_response(
            "Сообщение с указанным message_id не найдено.", "KIZ import message 123 не найдено"
        ),
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
}

PROCESS_DOCUMENTATION = {
    "summary": "Зарегистрировать КИЗ из сохранённого сообщения поступления",
    "description": (
        "Применяет или повторяет сообщение из журнала. Включённый consumer автоматически выполняет ту же регистрацию после сохранения доставки. Тело HTTP-запроса не требуется: данные берутся "
        "из сохранённого raw_payload. Ожидается объект с `supply.order_guid`, необязательными "
        "`supply_number` и `organization`, массивом `wild_groups` из объектов `wild` и "
        "`mark_codes`. Допускается временный аналог `supply.supply_guid`; если оба GUID переданы, "
        "они должны совпадать. Для mark_codes используются буквально первые 24 символа; короткие коды сохраняются целиком. Пробелы и регистр не изменяются, дубликаты проверяются после сокращения.\n\n"
        "Товар должен существовать. Поступление, его количество, приёмочная локация и физический остаток "
        "не ограничивают регистрацию. GUID — информационная связь; поступление может появиться позже. "
        "receipt_quantity равен null без поступления, снимки остатков равны null без настроенной локации. "
        "Новые КИЗ получают `registered`, `origin_type=receipt_import`, "
        "`origin_reference=order_guid`, `location_id=NULL`, `container_id=NULL`.\n\n"
        "Регистрация не создаёт движений, не меняет поступление и физический остаток, не уменьшает "
        "unidentified и не резервирует товар для КИЗ. Организация необязательна, сохраняется "
        "в новых КИЗ и затем неизменяема; существующий null автоматически не заполняется. "
        "Существующий КИЗ другого товара/происхождения или с другой заполненной организацией "
        "отклоняет всё сообщение. Конечные состояния не реактивируются.\n\n"
        "Обработка атомарна для всех товаров. Бизнес-ошибка сохраняет `rejected` и её причину "
        "без частично созданных КИЗ; после устранения причины можно повторить запрос. "
        "Повтор applied-сообщения возвращает `new_kiz_count=0` и текущие количества связанных "
        "существующих/конечных КИЗ. Значения физических остатков при повторе остаются снимком "
        "первоначальной обработки. Другой GUID того же КИЗ, товара и организации не создаёт новую identity: "
        "исходный origin_reference сохраняется, добавляется связь с сообщением. ACK следует после applied "
        "или сохранения rejected; системная ошибка вызывает requeue. Старые pending/rejected сообщения "
        "повторяются через этот endpoint, автоматического обхода backlog нет."
    ),
    "responses": {
        200: {
            "description": "Сообщение применено со статусом applied или возвращён результат повторной обработки.",
            "content": {"application/json": {"example": PROCESS_EXAMPLE}},
        },
        404: error_response(
            "Сообщение не найдено.",
            "KIZ import message 123 не найдено",
            "KIZ_IMPORT_MESSAGE_NOT_FOUND",
        ),
        409: error_response(
            "Конкурентное изменение данных; запрос можно повторить.",
            "Состояние КИЗ изменилось конкурентно; повторите обработку",
            "KIZ_IMPORT_CONCURRENT_CONFLICT",
        ),
        422: {
            "description": (
                "Ошибка message_id (detail — список) либо бизнес-проверки (detail — строка, "
                "error_code — причина). Коды формата: INVALID_RAW_PAYLOAD, INVALID_SUPPLY, "
                "ORDER_GUID_MISMATCH, ORDER_GUID_REQUIRED, INVALID_WILD_GROUPS, INVALID_WILD_GROUP, "
                "INVALID_PRODUCT_ID, INVALID_MARK_CODES, INVALID_KIZ_CODE, DUPLICATE_KIZ_CODE, "
                "INVALID_ORGANIZATION. Коды данных: UNKNOWN_PRODUCT, KIZ_OWNERSHIP_CONFLICT, "
                "KIZ_ORGANIZATION_CONFLICT. Для бизнес-ошибки сообщение получает статус rejected."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "business": {
                            "summary": "Неизвестный товар",
                            "value": {
                                "detail": "Неизвестный товар: testwild",
                                "error_code": "UNKNOWN_PRODUCT",
                            },
                        },
                        "validation": {
                            "summary": "Недопустимый идентификатор сообщения",
                            "value": VALIDATION_RESPONSE["content"]["application/json"]["example"],
                        },
                    }
                }
            },
        },
        500: SERVER_ERROR_RESPONSE,
    },
}
