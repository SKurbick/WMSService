"""Shared validation for values serialized to PostgreSQL jsonb."""


def validate_jsonb_value(value):
    """Reject text PostgreSQL/jsonb cannot store, including nested keys."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if "\x00" in item or any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                raise ValueError(
                    "JSON не должен содержать NUL или некорректные Unicode-символы"
                )
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return value
