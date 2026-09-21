"""Canonical identity for idempotent container physical operations."""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping

from app.core.json_validation import validate_jsonb_value


class ContainerOperationDisposition(StrEnum):
    NEW = "new"
    REPLAY = "replay"


@dataclass(frozen=True)
class ContainerOperationAcquisition:
    disposition: ContainerOperationDisposition
    operation: dict[str, Any]


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("Decimal в container intent должен быть конечным")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _canonicalize(value: Any, *, field_name: str | None = None):
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, float):
        raise ValueError("float запрещён в container intent; используйте Decimal")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Ключи container intent должны быть строками")
        return {key: _canonicalize(value[key], field_name=key) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        normalized = [_canonicalize(item) for item in value]
        if field_name == "kiz_codes":
            if not all(isinstance(item, str) and item for item in normalized):
                raise ValueError("kiz_codes должны быть непустыми строками")
            return sorted(normalized)
        if field_name == "items":
            if not all(isinstance(item, dict) for item in normalized):
                raise ValueError("items container intent должны быть объектами")
            line_ids = [item.get("external_line_id") for item in normalized]
            if not all(isinstance(line_id, str) and line_id for line_id in line_ids):
                raise ValueError("external_line_id обязателен для каждого item")
            if len(line_ids) != len(set(line_ids)):
                raise ValueError("duplicate external_line_id в container intent запрещён")
            return sorted(normalized, key=lambda item: item["external_line_id"])
        return normalized
    raise ValueError(f"Неподдерживаемый тип container intent: {type(value).__name__}")


def container_operation_fingerprint(intent: Mapping[str, Any]) -> str:
    validate_jsonb_value(intent)
    canonical = json.dumps(
        _canonicalize(intent),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
