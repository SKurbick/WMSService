"""Literal KIZ identity shared by ingestion, APIs and idempotency."""

from typing import Annotated

from pydantic import BeforeValidator, StringConstraints

from app.core.json_validation import validate_jsonb_value


KIZ_CODE_DESCRIPTION = (
    "КИЗ произвольной длины: для записи, поиска и сравнения используются буквально "
    "первые 24 символа. Короткий код сохраняется целиком. Пробелы, управляющие "
    "символы и регистр не изменяются. Пустой код, NUL и некорректный Unicode запрещены."
)


def normalize_kiz_code(value):
    if not isinstance(value, str):
        return value
    code = value[:24]
    validate_jsonb_value(code)
    return code


KizCode = Annotated[str, BeforeValidator(normalize_kiz_code), StringConstraints(min_length=1)]


def normalize_kiz_payload(value):
    if isinstance(value, dict):
        return {
            key: normalize_kiz_code(item)
            if key == "kiz_code" and isinstance(item, str)
            else [normalize_kiz_code(code) for code in item]
            if key == "kiz_codes" and isinstance(item, list)
            else normalize_kiz_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_kiz_payload(item) for item in value]
    return value


def restore_legacy_kiz_intent(intent, result):
    codes = {}

    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get("kiz_code"), str):
                code = value["kiz_code"]
                codes[normalize_kiz_code(code)] = code
            for code in value.get("kiz_codes", []):
                codes[normalize_kiz_code(code)] = code
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    def restore(value):
        if isinstance(value, dict):
            return {
                key: [codes.get(normalize_kiz_code(code), code) for code in item]
                if key == "kiz_codes" and isinstance(item, list)
                else restore(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [restore(item) for item in value]
        return value

    collect(result)
    return restore(intent)
