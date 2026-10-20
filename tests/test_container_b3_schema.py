"""Request, fingerprint and migration contracts for container B3."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.container_operation_idempotency import container_operation_fingerprint
from app.core.schemas.container_operations import ContainerMoveRequest, ContainerUnpackAllRequest


def move_request(**overrides):
    payload = {
        "source_system": "manual",
        "external_operation_id": "move-1",
        "author": "operator",
        "container_id": 1,
        "to_location_code": "B-01",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "field,value",
    [
        ("container_id", 0),
        ("container_id", "1"),
        ("to_location_code", ""),
        ("to_location_code", " B-01"),
        ("source_system", "manual "),
    ],
)
def test_move_request_rejects_invalid_identity(field, value):
    with pytest.raises(ValidationError):
        ContainerMoveRequest.model_validate(move_request(**{field: value}))


def test_unpack_all_forbids_unknown_fields():
    payload = move_request()
    payload.pop("to_location_code")
    payload["quantity"] = "1"
    with pytest.raises(ValidationError):
        ContainerUnpackAllRequest.model_validate(payload)


def test_b3_fingerprints_are_operation_specific_and_destination_sensitive():
    first = {"operation_type": "move", "container_id": 1, "to_location_code": "B"}
    assert container_operation_fingerprint(first) != container_operation_fingerprint(
        {**first, "to_location_code": "C"}
    )
    assert container_operation_fingerprint(first) != container_operation_fingerprint(
        {"operation_type": "unpack_all", "container_id": 1}
    )


def test_b3_migration_is_schema_protocol_only_before_function_definitions():
    sql = (
        (
            Path(__file__).resolve().parents[1]
            / "scripts/migrations/20260916_add_container_b3_move_unpack_all.sql"
        )
        .read_text(encoding="utf-8")
        .lower()
    )
    structural = sql.split("create or replace function", 1)[0]
    assert "insert into wms.movements" not in structural
    assert "update wms.inventory" not in sql
    assert "update wms.containers set location_id" not in sql
    assert "delete from wms.container_contents" not in structural


def test_b3_preflight_is_read_only():
    sql = (
        (
            Path(__file__).resolve().parents[1]
            / "scripts/migrations/20260916_container_b3_move_unpack_all_preflight.sql"
        )
        .read_text(encoding="utf-8")
        .lower()
    )
    assert "read only" in sql
    assert "insert into" not in sql
    assert "update wms." not in sql
    assert "delete from" not in sql


def test_b3_migration_suspends_old_operation_guard_only_for_locked_backfill():
    sql = (
        (
            Path(__file__).resolve().parents[1]
            / "scripts/migrations/20260916_add_container_b3_move_unpack_all.sql"
        )
        .read_text(encoding="utf-8")
        .lower()
    )
    lock = sql.index("lock table wms.container_operations")
    drop = sql.index("drop trigger trg_container_operations_guard")
    backfill = sql.index("update wms.container_operations o")
    recreate = sql.index("create trigger trg_container_operations_guard")
    commit = sql.rindex("commit;")
    assert lock < drop < backfill < recreate < commit
