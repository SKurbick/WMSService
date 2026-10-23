"""Схемы журнала сообщений и регистрации КИЗ поступления."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class KizImportMessageListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: int = Field(description="Идентификатор сохранённой доставки сообщения.")
    received_at: datetime = Field(description="Дата и время получения сообщения с часовым поясом.")
    parse_status: str = Field(
        description="Разбор JSON: received — корректный JSON, invalid_json — ошибка разбора. received не гарантирует корректность бизнес-полей."
    )
    order_guid: str | None = Field(
        None,
        description="GUID поступления из supply.order_guid; при отсутствии используется временный аналог supply.supply_guid.",
    )
    supply_number: str | None = Field(
        None, description="Номер поставки из supply.supply_number; диагностическое поле."
    )
    organization: str | None = Field(
        None,
        description="Организация из supply.organization без пробелов по краям; отсутствие поля соответствует null.",
    )
    exchange_name: str | None = Field(
        None, description="Имя обменника RabbitMQ, из которого доставлено сообщение."
    )
    routing_key: str | None = Field(None, description="Ключ маршрутизации доставки RabbitMQ.")
    wild_group_count: int = Field(
        description="Количество товарных групп во входном сообщении до бизнес-проверок."
    )
    mark_code_count: int = Field(
        description="Количество переданных кодов маркировки до проверки дубликатов и корректности."
    )
    business_status: str = Field(
        description="Обработка: pending — ещё не применено, applied — применено полностью или частично (см. business_result.is_partial/rejected_groups), rejected — сообщение целиком отклонено."
    )
    processed_at: datetime | None = Field(
        None, description="Дата и время обработки сообщения; до обработки null."
    )
    business_error_code: str | None = Field(
        None, description="Машинный код причины отклонения; при отсутствии ошибки null."
    )
    business_error_message: str | None = Field(
        None, description="Текст причины отклонения сообщения."
    )
    business_result: dict[str, Any] | None = Field(
        None,
        description="Сохранённый результат успешной обработки. Это снимок первоначальной обработки, а не текущие остатки.",
    )


class KizImportMessageDetail(KizImportMessageListItem):
    raw_body: str = Field(
        description="Исходное тело сообщения. Для байтов вне UTF-8 сохраняется представление base64."
    )
    raw_payload: Any | None = Field(
        None,
        description="Разобранный исходный JSON без переписывания полей; при ошибке разбора null.",
    )
    parse_error: str | None = Field(
        None, description="Ошибка разбора JSON или кодировки; при успешном разборе null."
    )
    rabbit_message_id: str | None = Field(
        None,
        description="Идентификатор сообщения, переданный отправителем RabbitMQ; может отсутствовать.",
    )
    correlation_id: str | None = Field(
        None, description="Идентификатор корреляции из свойств RabbitMQ."
    )
    headers: dict[str, Any] = Field(
        default_factory=dict, description="Сохранённые заголовки доставки RabbitMQ."
    )
    created_at: datetime = Field(
        description="Дата и время создания записи журнала с часовым поясом."
    )


class KizImportGroupResult(BaseModel):
    product_id: str = Field(description="Идентификатор товара из wild.")
    receipt_quantity: Decimal | None = Field(
        description="Информационное количество товара в поступлении; null, если поступление ещё не найдено. Не ограничивает регистрацию."
    )
    requested_codes: int = Field(description="Количество кодов товара во входном сообщении.")
    new_kiz: int = Field(
        description="Количество новых зарегистрированных КИЗ; при повторе применённого сообщения равно 0."
    )
    existing_kiz: int = Field(
        description="Количество связанных существующих КИЗ в состояниях registered или active."
    )
    terminal_existing: int = Field(
        description="Количество существующих КИЗ в конечных состояниях; они не активируются повторно."
    )
    physical_quantity: Decimal | None = Field(
        description="Снимок доступной россыпи без партии в настроенной приёмочной локации; null, если локация не найдена. На регистрацию не влияет."
    )
    identified_quantity: int | None = Field(
        description="Количество active КИЗ в этом физическом остатке на момент первоначальной обработки; registered КИЗ не учитываются."
    )
    unidentified_quantity: Decimal | None = Field(
        description="Неидентифицированный остаток: physical_quantity минус identified_quantity. При повторе возвращается первоначальный снимок."
    )


class KizImportGroupRejection(BaseModel):
    group_index: int = Field(description="Индекс отклонённой группы в исходном wild_groups, начиная с 0.")
    product_id: str | None = Field(None, description="Переданный wild; null при некорректной группе.")
    error_code: str
    error_message: str


class KizImportProcessResult(BaseModel):
    message_id: int = Field(description="Идентификатор применённого сообщения.")
    business_status: str = Field(description="Успешное состояние обработки: applied.")
    order_guid: str = Field(
        description="Информационный GUID поставки, записанный в origin_reference новых КИЗ. Поступление может появиться позже."
    )
    supply_number: str | None = Field(
        None, description="Номер поставки из сообщения, если передан."
    )
    organization: str | None = Field(
        None,
        description="Организация из сообщения, сохраняемая в новых КИЗ; null для старого формата.",
    )
    new_kiz_count: int = Field(
        description="Всего создано registered КИЗ этим вызовом; повтор применённого сообщения возвращает 0."
    )
    existing_kiz_count: int = Field(
        description="Всего связанных существующих registered и active КИЗ; при повторе определяется по текущему состоянию связей."
    )
    terminal_existing_count: int = Field(
        description="Всего связанных КИЗ в конечных состояниях; при повторе определяется по текущему состоянию связей."
    )
    groups: list[KizImportGroupResult] = Field(
        description="Результат регистрации и снимок остатков по каждому товару."
    )
    is_partial: bool = Field(False, description="true, если валидные группы применены, а часть групп отклонена.")
    rejected_groups: list[KizImportGroupRejection] = Field(
        default_factory=list, description="Отклонённые группы и причины. Для исправления отправьте новое сообщение."
    )


class KizReceiptCapacityViolation(BaseModel):
    order_guid: str = Field(
        description="Информационный GUID поступления с расхождением количества."
    )
    product_id: str = Field(description="Идентификатор товара с расхождением количества.")
    current_kiz_count: int = Field(
        description="Количество registered и active КИЗ поступления по этому товару."
    )
    receipt_quantity: Decimal = Field(
        description="Информационное количество товара в поступлении; не ограничивает регистрацию."
    )


class KizOrphanReceiptOrigin(BaseModel):
    kiz_id: int = Field(description="Идентификатор КИЗ без соответствующей строки поступления.")
    kiz_code: str = Field(description="Код КИЗ без соответствующей строки поступления.")
    order_guid: str = Field(description="GUID поступления, указанный в происхождении КИЗ.")
    product_id: str = Field(description="Идентификатор товара КИЗ.")
    lifecycle_status: str = Field(description="Текущее состояние жизненного цикла КИЗ.")


class KizRegisteredHolderViolation(BaseModel):
    kiz_id: int = Field(description="Идентификатор registered КИЗ с недопустимым местом хранения.")
    kiz_code: str = Field(description="Код зарегистрированного КИЗ с нарушением.")
    product_id: str = Field(description="Идентификатор товара КИЗ.")
    location_id: int | None = Field(
        None, description="Ошибочно назначенная локация; у registered КИЗ должна быть null."
    )
    container_id: int | None = Field(
        None, description="Ошибочно назначенный контейнер; у registered КИЗ должен быть null."
    )


class KizRegisteredOriginViolation(BaseModel):
    kiz_id: int = Field(description="Идентификатор registered КИЗ с некорректным происхождением.")
    kiz_code: str = Field(description="Код КИЗ с некорректным происхождением.")
    product_id: str = Field(description="Идентификатор товара КИЗ.")
    origin_type: str = Field(
        description="Фактический источник КИЗ; для registered ожидается receipt_import."
    )
    origin_reference: str | None = Field(
        None,
        description="Фактическая ссылка на происхождение; ожидается непустой GUID поступления.",
    )


class KizInvalidMessageLink(BaseModel):
    message_id: int = Field(description="Идентификатор сообщения с некорректной связью на КИЗ.")
    kiz_id: int = Field(description="Идентификатор связанного КИЗ.")
    message_order_guid: str | None = Field(None, description="GUID поступления из сообщения.")
    message_organization: str | None = Field(None, description="Организация из сообщения.")
    origin_type: str = Field(description="Фактический источник связанного КИЗ.")
    origin_reference: str | None = Field(
        None,
        description="Исходный информационный GUID КИЗ; может отличаться от GUID повторного сообщения.",
    )
    kiz_organization: str | None = Field(
        None,
        description="Организация КИЗ для сравнения с организацией сообщения, когда обе заполнены.",
    )


class KizImportIntegrityResponse(BaseModel):
    is_valid: bool = Field(
        description="true, если нет нарушений holder, origin и связей сообщений. Отсутствие поступления и превышение его количества информационны и не влияют на is_valid. HTTP 200 сохраняется."
    )
    receipt_capacity_violations: list[KizReceiptCapacityViolation] = Field(
        description="Информационные превышения количества КИЗ над количеством поступления; не являются ошибкой регистрации."
    )
    orphan_receipt_kiz: list[KizOrphanReceiptOrigin] = Field(
        description="Информационный список КИЗ без найденной строки поступления; поступление может появиться позже."
    )
    registered_holder_violations: list[KizRegisteredHolderViolation] = Field(
        description="Registered КИЗ с назначенной локацией или контейнером."
    )
    registered_origin_violations: list[KizRegisteredOriginViolation] = Field(
        description="Registered КИЗ с некорректным типом или ссылкой происхождения."
    )
    invalid_message_kiz_links: list[KizInvalidMessageLink] = Field(
        description="Связи сообщений и КИЗ с несовпадающими происхождением или заполненными организациями."
    )
