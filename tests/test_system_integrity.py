from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import (
    NegativeCalculatedInventoryError,
    RecalculateInventoryFromDateNotAllowedError,
)
from app.core.schemas.system import IntegrityCheckResult, RecalculateInventoryRequest
from app.core.services.system_service import SystemService
from app.infrastructure.database.queries import system as system_queries
from app.infrastructure.database.queries.system import VALIDATE_INTEGRITY
from app.infrastructure.database.repositories.system_repository import SystemRepository

EPSILON = Decimal("0.0001")


def _inventory_key(row):
    return (
        row["product_id"],
        row["location_id"],
        row.get("status", "available"),
        row.get("batch_number"),
        row.get("container_code"),
    )


def _expected_integrity_differences(movements, inventory):
    movement_totals = {}
    inventory_totals = {}

    for movement in movements:
        base = {
            "product_id": movement["product_id"],
            "status": "available",
            "batch_number": movement.get("batch_number"),
            "container_code": movement.get("container_code"),
        }
        quantity = Decimal(str(movement["quantity"]))

        if movement.get("to_location_id") is not None:
            key = _inventory_key({**base, "location_id": movement["to_location_id"]})
            movement_totals[key] = movement_totals.get(key, Decimal("0")) + quantity

        if movement.get("from_location_id") is not None:
            key = _inventory_key({**base, "location_id": movement["from_location_id"]})
            movement_totals[key] = movement_totals.get(key, Decimal("0")) - abs(quantity)

    movement_totals = {
        key: quantity for key, quantity in movement_totals.items() if abs(quantity) > EPSILON
    }

    for row in inventory:
        key = _inventory_key(row)
        inventory_totals[key] = inventory_totals.get(key, Decimal("0")) + Decimal(
            str(row["quantity"])
        )

    differences = []
    for key in sorted(set(movement_totals) | set(inventory_totals)):
        from_movements = movement_totals.get(key, Decimal("0"))
        from_inventory = inventory_totals.get(key, Decimal("0"))
        difference = from_movements - from_inventory
        if abs(difference) > EPSILON:
            differences.append(
                {
                    "product_id": key[0],
                    "location_id": key[1],
                    "status": key[2],
                    "batch_number": key[3],
                    "container_code": key[4],
                    "from_movements": from_movements,
                    "from_inventory": from_inventory,
                    "difference": difference,
                }
            )

    return differences


def test_validate_integrity_sql_uses_net_movement_ledger():
    sql = VALIDATE_INTEGRITY

    assert "WITH movement_ledger AS" in sql
    assert "to_location_id as location_id" in sql
    assert "from_location_id as location_id" in sql
    assert "quantity as signed_quantity" in sql
    assert "-ABS(quantity) as signed_quantity" in sql
    assert "UNION ALL" in sql
    assert "FULL OUTER JOIN current_inventory" in sql
    assert "IS NOT DISTINCT FROM" in sql
    assert "ABS(COALESCE(ci.calculated_quantity, 0)" in sql
    assert "COALESCE(to_location_id, from_location_id)" not in sql


def test_validate_integrity_sql_groups_by_available_inventory_identity():
    sql = VALIDATE_INTEGRITY

    assert "FROM wms.inventory\n    WHERE status = 'available'" in sql
    assert "GROUP BY product_id, location_id, status, batch_number, container_code" in sql
    assert "ci.status = i.status" in sql
    assert "ci.batch_number IS NOT DISTINCT FROM i.batch_number" in sql
    assert "ci.container_code IS NOT DISTINCT FROM i.container_code" in sql


def test_integrity_schema_accepts_status_without_breaking_existing_fields():
    result = IntegrityCheckResult.model_validate(
        {
            "product_id": "wild1983",
            "location_code": "PUSHKINO-RECEIVING",
            "status": "available",
            "batch_number": None,
            "container_code": None,
            "from_movements": 10,
            "from_inventory": 8,
            "difference": 2,
        }
    )

    assert result.status == "available"
    assert result.from_movements == 10
    assert result.difference == 2


def test_full_inbound_and_outbound_with_zero_inventory_has_no_difference():
    movements = [
        {"product_id": "sku", "to_location_id": 1, "quantity": 100},
        {"product_id": "sku", "from_location_id": 1, "quantity": 100},
    ]
    inventory = [{"product_id": "sku", "location_id": 1, "quantity": 0}]

    assert _expected_integrity_differences(movements, inventory) == []


def test_partial_outbound_matching_inventory_has_no_difference():
    movements = [
        {"product_id": "sku", "to_location_id": 1, "quantity": 100},
        {"product_id": "sku", "from_location_id": 1, "quantity": 30},
    ]
    inventory = [{"product_id": "sku", "location_id": 1, "quantity": 70}]

    assert _expected_integrity_differences(movements, inventory) == []


def test_inventory_quantity_mismatch_reports_difference():
    movements = [
        {"product_id": "sku", "to_location_id": 1, "quantity": 100},
        {"product_id": "sku", "from_location_id": 1, "quantity": 30},
    ]
    inventory = [{"product_id": "sku", "location_id": 1, "quantity": 60}]

    assert _expected_integrity_differences(movements, inventory) == [
        {
            "product_id": "sku",
            "location_id": 1,
            "status": "available",
            "batch_number": None,
            "container_code": None,
            "from_movements": Decimal("70"),
            "from_inventory": Decimal("60"),
            "difference": Decimal("10"),
        }
    ]


def test_movements_without_inventory_report_positive_difference():
    movements = [{"product_id": "sku", "to_location_id": 1, "quantity": 50}]

    assert _expected_integrity_differences(movements, []) == [
        {
            "product_id": "sku",
            "location_id": 1,
            "status": "available",
            "batch_number": None,
            "container_code": None,
            "from_movements": Decimal("50"),
            "from_inventory": Decimal("0"),
            "difference": Decimal("50"),
        }
    ]


def test_inventory_without_movements_reports_negative_difference():
    inventory = [{"product_id": "sku", "location_id": 1, "quantity": 20}]

    assert _expected_integrity_differences([], inventory) == [
        {
            "product_id": "sku",
            "location_id": 1,
            "status": "available",
            "batch_number": None,
            "container_code": None,
            "from_movements": Decimal("0"),
            "from_inventory": Decimal("20"),
            "difference": Decimal("-20"),
        }
    ]


def test_null_and_non_null_batches_are_not_mixed():
    movements = [
        {"product_id": "sku", "to_location_id": 1, "quantity": 10},
        {
            "product_id": "sku",
            "to_location_id": 1,
            "quantity": 5,
            "batch_number": "batch-1",
        },
    ]
    inventory = [
        {"product_id": "sku", "location_id": 1, "quantity": 10},
        {
            "product_id": "sku",
            "location_id": 1,
            "quantity": 5,
            "batch_number": "batch-1",
        },
    ]

    assert _expected_integrity_differences(movements, inventory) == []


def test_null_and_non_null_container_codes_are_not_mixed():
    movements = [
        {"product_id": "sku", "to_location_id": 1, "quantity": 10},
        {
            "product_id": "sku",
            "to_location_id": 1,
            "quantity": 7,
            "container_code": "BOX-1",
        },
    ]
    inventory = [{"product_id": "sku", "location_id": 1, "quantity": 17}]

    differences = _expected_integrity_differences(movements, inventory)

    assert len(differences) == 2
    assert {row["container_code"] for row in differences} == {None, "BOX-1"}


def _expected_recalculated_available_inventory(movements):
    totals = {}
    for movement in movements:
        base = {
            "product_id": movement["product_id"],
            "status": "available",
            "batch_number": movement.get("batch_number"),
            "container_code": movement.get("container_code"),
        }
        quantity = Decimal(str(movement["quantity"]))

        if movement.get("to_location_id") is not None:
            key = _inventory_key({**base, "location_id": movement["to_location_id"]})
            totals[key] = totals.get(key, Decimal("0")) + quantity

        if movement.get("from_location_id") is not None:
            key = _inventory_key({**base, "location_id": movement["from_location_id"]})
            totals[key] = totals.get(key, Decimal("0")) - abs(quantity)

    negative_rows = {key: quantity for key, quantity in totals.items() if quantity < -EPSILON}
    if negative_rows:
        return negative_rows

    return {key: quantity for key, quantity in totals.items() if quantity > EPSILON}


def test_recalculate_sql_uses_available_net_ledger_and_positive_insert_only():
    sql = system_queries.RECALCULATE_INVENTORY

    assert "to_location_id as location_id" in sql
    assert "from_location_id as location_id" in sql
    assert "quantity as signed_quantity" in sql
    assert "-ABS(quantity) as signed_quantity" in sql
    assert "'available'::varchar as status" in sql
    assert "GROUP BY product_id, location_id, status, batch_number, container_code" in sql
    assert "WHERE calculated_quantity > 0.0001" in sql
    assert "COALESCE(m.to_location_id, m.from_location_id)" not in sql
    assert "COALESCE(batch_number" not in sql
    assert "COALESCE(container_code" not in sql
    assert "created_at >=" not in sql


def test_recalculate_delete_and_stats_are_available_only():
    assert "WHERE status = 'available'" in system_queries.DELETE_AVAILABLE_INVENTORY
    assert "WHERE status = 'available'" in system_queries.GET_INVENTORY_STATS
    assert "damaged" not in system_queries.DELETE_AVAILABLE_INVENTORY
    assert "quarantine" not in system_queries.DELETE_AVAILABLE_INVENTORY


def test_recalculate_negative_diagnostic_query_returns_identity_fields():
    sql = system_queries.CHECK_NEGATIVE_CALCULATED_INVENTORY

    assert "WHERE calculated_quantity < -0.0001" in sql
    assert "product_id" in sql
    assert "location_id" in sql
    assert "batch_number" in sql
    assert "container_code" in sql
    assert "calculated_quantity" in sql


def test_recalculate_receive_creates_available_quantity():
    result = _expected_recalculated_available_inventory(
        [{"product_id": "sku", "to_location_id": 1, "quantity": 100}]
    )

    assert result == {
        ("sku", 1, "available", None, None): Decimal("100"),
    }


def test_recalculate_transfer_decreases_source_and_increases_destination():
    result = _expected_recalculated_available_inventory(
        [
            {"product_id": "sku", "to_location_id": 1, "quantity": 100},
            {
                "product_id": "sku",
                "from_location_id": 1,
                "to_location_id": 2,
                "quantity": 40,
            },
        ]
    )

    assert result == {
        ("sku", 1, "available", None, None): Decimal("60"),
        ("sku", 2, "available", None, None): Decimal("40"),
    }


def test_recalculate_zero_quantity_is_not_inserted():
    result = _expected_recalculated_available_inventory(
        [
            {"product_id": "sku", "to_location_id": 1, "quantity": 100},
            {"product_id": "sku", "from_location_id": 1, "quantity": 100},
        ]
    )

    assert result == {}


def test_recalculate_ship_leaves_positive_available_quantity():
    result = _expected_recalculated_available_inventory(
        [
            {"product_id": "sku", "to_location_id": 1, "quantity": 100},
            {"product_id": "sku", "from_location_id": 1, "quantity": 30},
        ]
    )

    assert result == {
        ("sku", 1, "available", None, None): Decimal("70"),
    }


def test_recalculate_batch_null_and_not_null_are_not_mixed():
    result = _expected_recalculated_available_inventory(
        [
            {"product_id": "sku", "to_location_id": 1, "quantity": 10},
            {
                "product_id": "sku",
                "to_location_id": 1,
                "quantity": 5,
                "batch_number": "batch-1",
            },
        ]
    )

    assert result == {
        ("sku", 1, "available", None, None): Decimal("10"),
        ("sku", 1, "available", "batch-1", None): Decimal("5"),
    }


def test_recalculate_container_null_and_not_null_are_not_mixed():
    result = _expected_recalculated_available_inventory(
        [
            {"product_id": "sku", "to_location_id": 1, "quantity": 10},
            {
                "product_id": "sku",
                "to_location_id": 1,
                "quantity": 7,
                "container_code": "BOX-1",
            },
        ]
    )

    assert result == {
        ("sku", 1, "available", None, None): Decimal("10"),
        ("sku", 1, "available", None, "BOX-1"): Decimal("7"),
    }


def test_recalculate_negative_quantity_is_reported():
    result = _expected_recalculated_available_inventory(
        [{"product_id": "sku", "from_location_id": 1, "quantity": 30}]
    )

    assert result == {
        ("sku", 1, "available", None, None): Decimal("-30"),
    }


class FakeTransaction:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        self.calls.append(("transaction_enter",))

    async def __aexit__(self, exc_type, exc, tb):
        self.calls.append(("transaction_exit", exc_type))


class FakeConnection:
    def __init__(self, negative_rows=None):
        self.calls = []
        self.negative_rows = negative_rows or []

    def transaction(self):
        return FakeTransaction(self.calls)

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        return self.negative_rows

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))
        return "OK"

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        return {"inventory_records": 1, "total_units": 70, "products_count": 1}


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)


@pytest.mark.asyncio
async def test_recalculate_repository_runs_check_delete_insert_stats_in_one_transaction():
    conn = FakeConnection()
    repo = SystemRepository(FakePool(conn))

    result = await repo.recalculate_inventory(product_id="sku")

    assert result == {"inventory_records": 1, "total_units": 70, "products_count": 1}
    assert conn.calls == [
        ("transaction_enter",),
        ("fetch", system_queries.CHECK_NEGATIVE_CALCULATED_INVENTORY, ("sku",)),
        ("execute", system_queries.DELETE_AVAILABLE_INVENTORY, ("sku",)),
        ("execute", system_queries.RECALCULATE_INVENTORY, ("sku",)),
        ("fetchrow", system_queries.GET_INVENTORY_STATS, ("sku",)),
        ("transaction_exit", None),
    ]


@pytest.mark.asyncio
async def test_recalculate_repository_stops_before_delete_when_negative_calculated_quantity():
    conn = FakeConnection(
        negative_rows=[
            {
                "product_id": "sku",
                "location_id": 1,
                "batch_number": None,
                "container_code": "BOX-1",
                "calculated_quantity": Decimal("-30"),
            }
        ]
    )
    repo = SystemRepository(FakePool(conn))

    with pytest.raises(NegativeCalculatedInventoryError) as exc_info:
        await repo.recalculate_inventory()

    assert "product_id=sku" in str(exc_info.value)
    assert "location_id=1" in str(exc_info.value)
    assert "container_code=BOX-1" in str(exc_info.value)
    assert "calculated_quantity=-30" in str(exc_info.value)
    assert [call[0] for call in conn.calls] == [
        "transaction_enter",
        "fetch",
        "transaction_exit",
    ]


@pytest.mark.asyncio
async def test_recalculate_service_rejects_from_date_before_repository_call():
    class RepositoryShouldNotBeCalled:
        async def recalculate_inventory(self, **kwargs):
            raise AssertionError("repository should not be called")

    service = SystemService(RepositoryShouldNotBeCalled())

    with pytest.raises(RecalculateInventoryFromDateNotAllowedError) as exc_info:
        await service.recalculate_inventory(
            RecalculateInventoryRequest(from_date=date(2026, 1, 1))
        )

    assert "from_date" in str(exc_info.value)
    assert "полный пересчет available" in str(exc_info.value)
