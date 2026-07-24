"""Общий read-only codec event ID и названий операций."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


OPERATION_NAMES: dict[str, str] = {
    "receive": "Поступление",
    "putaway": "Размещение",
    "transfer": "Перемещение",
    "pick": "Отбор",
    "ship": "Отгрузка",
    "unpack": "Распаковка",
    "adjust": "Корректировка",
    "kit_assembly": "Комплектация",
    "kit_disassembly": "Разукомплектация",
    "re_sorting": "Пересортица",
    "fbs_shipment": "ФБС-отгрузка",
}

_PREFIX_PARTS = {
    "kit_operation": 2,
    "re_sorting": 2,
    "fbs_shipment": 2,
    "movement": 3,
}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class ParsedOperationEventId:
    prefix: str
    entity_id: int
    movement_created_at: datetime | None = None


def _positive_integer(value: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label} должен быть положительным целым числом")
    return value


def get_operation_name(operation_type: str) -> str:
    return OPERATION_NAMES.get(operation_type, operation_type)


def build_kit_event_id(operation_id: int) -> str:
    return f"kit_operation:{_positive_integer(operation_id, 'operation_id')}"


def build_re_sorting_event_id(operation_id: int) -> str:
    return f"re_sorting:{_positive_integer(operation_id, 'operation_id')}"


def build_fbs_event_id(shipment_id: int) -> str:
    return f"fbs_shipment:{_positive_integer(shipment_id, 'shipment_id')}"


def datetime_to_epoch_us(created_at: datetime) -> int:
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("created_at должен содержать timezone")
    delta = created_at.astimezone(timezone.utc) - _EPOCH
    epoch_us = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    return _positive_integer(epoch_us, "created_at_epoch_us")


def epoch_us_to_datetime(epoch_us: int) -> datetime:
    value = _positive_integer(epoch_us, "created_at_epoch_us")
    seconds, microseconds = divmod(value, 1_000_000)
    return _EPOCH + timedelta(seconds=seconds, microseconds=microseconds)


def build_movement_event_id(movement_id: int, created_at: datetime) -> str:
    return f"movement:{_positive_integer(movement_id, 'movement_id')}:{datetime_to_epoch_us(created_at)}"


def parse_operation_event_id(event_id: str) -> ParsedOperationEventId:
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id должен быть непустой строкой")
    parts = event_id.split(":")
    prefix = parts[0]
    expected_parts = _PREFIX_PARTS.get(prefix)
    if expected_parts is None:
        raise ValueError(f"Неподдерживаемый prefix event_id: {prefix}")
    if len(parts) != expected_parts:
        raise ValueError(f"Неверный формат event_id для prefix {prefix}")
    try:
        entity_id = int(parts[1])
    except ValueError as exc:
        raise ValueError("ID в event_id должен быть целым числом") from exc
    _positive_integer(entity_id, "ID")
    if prefix != "movement":
        return ParsedOperationEventId(prefix=prefix, entity_id=entity_id)
    try:
        epoch_us = int(parts[2])
    except ValueError as exc:
        raise ValueError("created_at_epoch_us должен быть целым числом") from exc
    return ParsedOperationEventId(
        prefix=prefix,
        entity_id=entity_id,
        movement_created_at=epoch_us_to_datetime(epoch_us),
    )
