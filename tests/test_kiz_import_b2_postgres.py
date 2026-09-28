import asyncio
import json
import os
from pathlib import Path

import asyncpg
import pytest

from app.core.exceptions import KizConflictError, KizImportBusinessError
from app.core.schemas.movement import MovementCreate
from app.core.services.kiz_import_service import KizImportService
from app.core.services.movement_service import MovementService
from app.infrastructure.database.repositories.kiz_import_repository import KizImportRepository
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.infrastructure.database.repositories.movement_repository import MovementRepository

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("KIZ_B2_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="KIZ_B2_TEST_DATABASE_URL is required for disposable PostgreSQL tests",
)


@pytest.fixture
async def pool():
    connection = await asyncpg.connect(DATABASE_URL)
    try:
        await connection.execute("DROP SCHEMA IF EXISTS wms CASCADE")
        await connection.execute("DROP TABLE IF EXISTS public.products")
        await connection.execute((ROOT / "tests/fixtures/kiz_b2_base_schema.sql").read_text())
        await connection.execute(
            (ROOT / "scripts/migrations/20260923_add_kiz_import_inbox.sql").read_text()
        )
        await connection.execute(
            (ROOT / "scripts/migrations/20260924_add_kiz_import_b2.sql").read_text()
        )
        await connection.execute(
            (ROOT / "scripts/migrations/20260928_kiz_receipt_b21_registered.sql").read_text()
        )
    finally:
        await connection.close()

    test_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=8)
    yield test_pool
    await test_pool.close()
    connection = await asyncpg.connect(DATABASE_URL)
    try:
        await connection.execute("DROP SCHEMA wms CASCADE")
        await connection.execute("DROP TABLE public.products")
    finally:
        await connection.close()


@pytest.fixture(autouse=True)
async def clean_database(pool):
    async with pool.acquire() as connection:
        await connection.execute(
            """
            TRUNCATE
                wms.kiz_import_message_kiz,
                wms.kiz_events,
                wms.kiz,
                wms.kiz_import_messages,
                wms.movements,
                wms.inventory,
                wms.receipt_items,
                wms.locations,
                public.products
            RESTART IDENTITY CASCADE
            """
        )


async def prepare_receipt(pool, quantities, *, order_guid="B2-ORDER-001"):
    async with pool.acquire() as connection:
        location_id = await connection.fetchval(
            """
            INSERT INTO wms.locations (location_code)
            VALUES ('PUSHKINO-ПРИЁМКА')
            RETURNING location_id
            """
        )
        for product_id, quantity in quantities.items():
            await connection.execute("INSERT INTO public.products (id) VALUES ($1)", product_id)
            await connection.execute(
                """
                INSERT INTO wms.receipt_items (guid, product_id, quantity)
                VALUES ($1, $2, $3)
                """,
                order_guid,
                product_id,
                quantity,
            )
            await connection.execute(
                """
                INSERT INTO wms.inventory (
                    product_id, location_id, quantity, status, batch_number, container_code
                )
                VALUES ($1, $2, $3, 'available', NULL, NULL)
                """,
                product_id,
                location_id,
                quantity,
            )
    return location_id


async def ingest_message(pool, groups, *, order_guid="B2-ORDER-001"):
    payload = {
        "supply": {"order_guid": order_guid, "supply_number": "B2-SUP-001"},
        "wild_groups": groups,
    }
    result = await KizImportService(KizImportRepository(pool)).ingest(
        json.dumps(payload, ensure_ascii=False).encode(),
        exchange_name="orders",
        routing_key="orders.kiz.imported",
        rabbit_message_id=None,
        correlation_id=None,
        headers={},
    )
    return result.message_id


async def ingest_payload(pool, payload):
    result = await KizImportService(KizImportRepository(pool)).ingest(
        json.dumps(payload, ensure_ascii=False).encode(),
        exchange_name="orders",
        routing_key="orders.kiz.imported",
        rabbit_message_id=None,
        correlation_id=None,
        headers={},
    )
    return result.message_id


@pytest.mark.asyncio
async def test_positive_mixed_and_multi_wild_has_no_physical_side_effects(pool):
    await prepare_receipt(pool, {"testwild": 10, "testwild2": 4})
    message_id = await ingest_message(
        pool,
        [
            {"wild": "testwild", "mark_codes": ["K1", 'dgfhgdgfg!"NAk', "K3"]},
            {"wild": "testwild2", "mark_codes": ["K4", "K5"]},
        ],
    )

    result = await KizImportService(KizImportRepository(pool)).process_message(
        message_id, "PUSHKINO-ПРИЁМКА"
    )

    assert result["new_kiz_count"] == 5
    assert result["groups"][0]["identified_quantity"] == 0
    assert result["groups"][0]["unidentified_quantity"] == "10"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM wms.movements") == 0
        assert (
            await connection.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='testwild'"
            )
            == 10
        )
        assert (
            await connection.fetchval(
                "SELECT quantity FROM wms.receipt_items WHERE product_id='testwild'"
            )
            == 10
        )
        rows = await connection.fetch("SELECT * FROM wms.kiz ORDER BY kiz_code")
        assert all(row["origin_type"] == "receipt_import" for row in rows)
        assert all(row["origin_reference"] == "B2-ORDER-001" for row in rows)
        assert all(row["lifecycle_status"] == "registered" for row in rows)
        assert all(row["location_id"] is None for row in rows)
        assert all(row["container_id"] is None for row in rows)
        events = await connection.fetch("SELECT * FROM wms.kiz_events ORDER BY kiz_event_id")
        assert all(row["event_type"] == "registered" for row in events)
        assert all(row["to_status"] == "registered" for row in events)
        assert all(row["location_id"] is None for row in events)
        assert all(row["movement_ref"] is None for row in events)
        assert all(
            json.loads(row["metadata"])["receipt_location_code"] == "PUSHKINO-ПРИЁМКА"
            for row in events
        )


@pytest.mark.asyncio
async def test_same_message_duplicate_row_and_additive_replay_are_idempotent(pool):
    await prepare_receipt(pool, {"testwild": 5})
    service = KizImportService(KizImportRepository(pool))
    first_id = await ingest_message(pool, [{"wild": "testwild", "mark_codes": ["K1", "K2"]}])
    first = await service.process_message(first_id, "PUSHKINO-ПРИЁМКА")
    same = await service.process_message(first_id, "PUSHKINO-ПРИЁМКА")
    duplicate_id = await ingest_message(pool, [{"wild": "testwild", "mark_codes": ["K1", "K2"]}])
    duplicate = await service.process_message(duplicate_id, "PUSHKINO-ПРИЁМКА")
    additive_id = await ingest_message(
        pool, [{"wild": "testwild", "mark_codes": ["K1", "K2", "K3"]}]
    )
    additive = await service.process_message(additive_id, "PUSHKINO-ПРИЁМКА")

    assert first["new_kiz_count"] == 2
    assert same["new_kiz_count"] == 0
    assert same["existing_kiz_count"] == 2
    assert duplicate["new_kiz_count"] == 0
    assert duplicate["existing_kiz_count"] == 2
    assert additive["new_kiz_count"] == 1
    assert additive["existing_kiz_count"] == 2
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz") == 3
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz_events") == 3
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz_import_message_kiz") == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quantity", "codes", "error_code"),
    [
        (2, ["K1", "K2", "K3"], "RECEIPT_QUANTITY_EXCEEDED"),
        (1, ["K1", "K2"], "RECEIPT_QUANTITY_EXCEEDED"),
    ],
)
async def test_capacity_conflict_rejects_without_partial_changes(pool, quantity, codes, error_code):
    await prepare_receipt(pool, {"testwild": quantity})
    message_id = await ingest_message(pool, [{"wild": "testwild", "mark_codes": codes}])

    with pytest.raises(KizImportBusinessError) as caught:
        await KizImportService(KizImportRepository(pool)).process_message(
            message_id, "PUSHKINO-ПРИЁМКА"
        )

    assert caught.value.error_code == error_code
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz") == 0
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz_events") == 0
        assert (
            await connection.fetchval(
                "SELECT business_status FROM wms.kiz_import_messages WHERE message_id=$1",
                message_id,
            )
            == "rejected"
        )


@pytest.mark.asyncio
async def test_registration_succeeds_with_zero_receipt_location_physical(pool):
    await prepare_receipt(pool, {"testwild": 3})
    async with pool.acquire() as connection:
        await connection.execute("DELETE FROM wms.inventory")
    message_id = await ingest_message(
        pool,
        [{"wild": "testwild", "mark_codes": ["K1", "K2"]}],
    )

    result = await KizImportService(KizImportRepository(pool)).process_message(
        message_id, "PUSHKINO-ПРИЁМКА"
    )

    assert result["new_kiz_count"] == 2
    assert result["groups"][0]["physical_quantity"] == "0"
    assert result["groups"][0]["identified_quantity"] == 0
    assert result["groups"][0]["unidentified_quantity"] == "0"
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT lifecycle_status, location_id, container_id FROM wms.kiz"
        )
        assert [dict(row) for row in rows] == [
            {"lifecycle_status": "registered", "location_id": None, "container_id": None},
            {"lifecycle_status": "registered", "location_id": None, "container_id": None},
        ]


@pytest.mark.asyncio
async def test_registered_and_active_receipt_identities_share_receipt_limit(pool):
    location_id = await prepare_receipt(pool, {"testwild": 3})
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO wms.kiz (
                kiz_code, product_id, location_id, lifecycle_status,
                origin_type, origin_reference, created_by
            )
            VALUES
                ('REGISTERED', 'testwild', NULL, 'registered',
                 'receipt_import', 'B2-ORDER-001', 'test'),
                ('ACTIVE', 'testwild', $1, 'active',
                 'receipt_import', 'B2-ORDER-001', 'test')
            """,
            location_id,
        )

    allowed_id = await ingest_message(pool, [{"wild": "testwild", "mark_codes": ["K3"]}])
    allowed = await KizImportService(KizImportRepository(pool)).process_message(
        allowed_id, "PUSHKINO-ПРИЁМКА"
    )
    assert allowed["new_kiz_count"] == 1

    rejected_id = await ingest_message(
        pool,
        [{"wild": "testwild", "mark_codes": ["K4", "K5"]}],
    )
    with pytest.raises(KizImportBusinessError) as caught:
        await KizImportService(KizImportRepository(pool)).process_message(
            rejected_id, "PUSHKINO-ПРИЁМКА"
        )
    assert caught.value.error_code == "RECEIPT_QUANTITY_EXCEEDED"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz") == 3


@pytest.mark.asyncio
async def test_conflicting_existing_kiz_rolls_back_all_products(pool):
    location_id = await prepare_receipt(pool, {"testwild": 2, "testwild2": 2})
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO wms.kiz (
                kiz_code, product_id, location_id, origin_type, origin_reference,
                created_by
            )
            VALUES ('CONFLICT', 'testwild2', $1, 'warehouse_assignment', NULL, 'test')
            """,
            location_id,
        )
    message_id = await ingest_message(
        pool,
        [
            {"wild": "testwild", "mark_codes": ["VALID"]},
            {"wild": "testwild2", "mark_codes": ["CONFLICT"]},
        ],
    )

    with pytest.raises(KizImportBusinessError) as caught:
        await KizImportService(KizImportRepository(pool)).process_message(
            message_id, "PUSHKINO-ПРИЁМКА"
        )

    assert caught.value.error_code == "KIZ_OWNERSHIP_CONFLICT"
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz WHERE kiz_code='VALID'") == 0
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz_events") == 0
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz_import_message_kiz") == 0


@pytest.mark.asyncio
async def test_concurrent_same_message_and_capacity_are_serialized(pool):
    await prepare_receipt(pool, {"testwild": 2})
    service = KizImportService(KizImportRepository(pool))
    same_id = await ingest_message(pool, [{"wild": "testwild", "mark_codes": ["K1", "K2"]}])
    same_results = await asyncio.gather(
        service.process_message(same_id, "PUSHKINO-ПРИЁМКА"),
        service.process_message(same_id, "PUSHKINO-ПРИЁМКА"),
    )
    assert sorted(result["new_kiz_count"] for result in same_results) == [0, 2]
    assert sorted(result["existing_kiz_count"] for result in same_results) == [0, 2]

    async with pool.acquire() as connection:
        await connection.execute(
            "TRUNCATE wms.kiz_import_message_kiz, wms.kiz_events, wms.kiz, "
            "wms.kiz_import_messages RESTART IDENTITY CASCADE"
        )
    first_id = await ingest_message(pool, [{"wild": "testwild", "mark_codes": ["A1", "A2"]}])
    second_id = await ingest_message(pool, [{"wild": "testwild", "mark_codes": ["B1", "B2"]}])
    outcomes = await asyncio.gather(
        service.process_message(first_id, "PUSHKINO-ПРИЁМКА"),
        service.process_message(second_id, "PUSHKINO-ПРИЁМКА"),
        return_exceptions=True,
    )
    assert sum(isinstance(item, KizImportBusinessError) for item in outcomes) == 1
    async with pool.acquire() as connection:
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz WHERE lifecycle_status='registered'"
            )
            == 2
        )


@pytest.mark.asyncio
async def test_integrity_reports_orphan_and_capacity_violations(pool):
    location_id = await prepare_receipt(pool, {"testwild": 1})
    async with pool.acquire() as connection:
        await connection.execute(
            "ALTER TABLE wms.kiz DISABLE TRIGGER trg_kiz_final_holder_integrity"
        )
        await connection.execute(
            """
            INSERT INTO wms.kiz (
                kiz_code, product_id, location_id, origin_type, origin_reference, created_by
            )
            VALUES
                ('K1', 'testwild', $1, 'receipt_import', 'B2-ORDER-001', 'test'),
                ('K2', 'testwild', $1, 'receipt_import', 'B2-ORDER-001', 'test'),
                ('ORPHAN', 'testwild', $1, 'receipt_import', 'missing', 'test')
            """,
            location_id,
        )
        await connection.execute(
            "ALTER TABLE wms.kiz ENABLE TRIGGER trg_kiz_final_holder_integrity"
        )

    result = await KizImportService(KizImportRepository(pool)).get_integrity()

    assert result["is_valid"] is False
    assert len(result["receipt_capacity_violations"]) == 1
    assert len(result["orphan_receipt_kiz"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        (
            {
                "supply": {"order_guid": "one", "supply_guid": "two"},
                "wild_groups": [],
            },
            "ORDER_GUID_MISMATCH",
        ),
        (
            {
                "supply": {"order_guid": "B2-ORDER-001"},
                "wild_groups": [
                    {"wild": "testwild", "mark_codes": ["DUP"]},
                    {"wild": "testwild2", "mark_codes": ["DUP"]},
                ],
            },
            "DUPLICATE_KIZ_CODE",
        ),
    ],
)
async def test_payload_validation_is_deterministic_and_persists_rejection(
    pool, payload, error_code
):
    message_id = await ingest_payload(pool, payload)

    with pytest.raises(KizImportBusinessError) as caught:
        await KizImportService(KizImportRepository(pool)).process_message(
            message_id, "PUSHKINO-ПРИЁМКА"
        )

    assert caught.value.error_code == error_code
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT business_status, business_error_code
            FROM wms.kiz_import_messages
            WHERE message_id=$1
            """,
            message_id,
        )
        assert dict(row) == {
            "business_status": "rejected",
            "business_error_code": error_code,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "order_guid", "product_id", "error_code"),
    [
        ("receipt", "missing", "testwild", "UNKNOWN_RECEIPT"),
        ("receipt", "B2-ORDER-001", "unknown", "UNKNOWN_PRODUCT"),
        ("multi", "B2-ORDER-001", "testwild2", "PRODUCT_NOT_IN_RECEIPT"),
    ],
)
async def test_unknown_receipt_product_and_receipt_line_are_rejected(
    pool, setup, order_guid, product_id, error_code
):
    await prepare_receipt(pool, {"testwild": 2})
    if setup == "multi":
        async with pool.acquire() as connection:
            await connection.execute("INSERT INTO public.products (id) VALUES ('testwild2')")
    message_id = await ingest_message(
        pool,
        [{"wild": product_id, "mark_codes": ["K1"]}],
        order_guid=order_guid,
    )

    with pytest.raises(KizImportBusinessError) as caught:
        await KizImportService(KizImportRepository(pool)).process_message(
            message_id, "PUSHKINO-ПРИЁМКА"
        )

    assert caught.value.error_code == error_code


@pytest.mark.asyncio
async def test_active_kiz_remain_identified_but_do_not_block_registration(pool):
    location_id = await prepare_receipt(pool, {"testwild": 5})
    async with pool.acquire() as connection:
        for number in range(4):
            await connection.execute(
                """
                INSERT INTO wms.kiz (
                    kiz_code, product_id, location_id, origin_type, created_by
                )
                VALUES ($1, 'testwild', $2, 'warehouse_assignment', 'test')
                """,
                f"ASSIGNED-{number}",
                location_id,
            )
    message_id = await ingest_message(pool, [{"wild": "testwild", "mark_codes": ["R1", "R2"]}])

    result = await KizImportService(KizImportRepository(pool)).process_message(
        message_id, "PUSHKINO-ПРИЁМКА"
    )

    assert result["new_kiz_count"] == 2
    assert result["groups"][0]["identified_quantity"] == 4
    assert result["groups"][0]["unidentified_quantity"] == "1"
    async with pool.acquire() as connection:
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz WHERE origin_type='receipt_import'"
            )
            == 2
        )


@pytest.mark.asyncio
async def test_registered_kiz_do_not_block_legacy_full_loose_transfer(pool):
    await prepare_receipt(pool, {"testwild": 3})
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO wms.locations (location_code) VALUES ('PUSHKINO-TEST')"
        )
    message_id = await ingest_message(
        pool,
        [{"wild": "testwild", "mark_codes": ["R1", "R2"]}],
    )
    await KizImportService(KizImportRepository(pool)).process_message(
        message_id, "PUSHKINO-ПРИЁМКА"
    )

    movement_result = await MovementService(
        MovementRepository(pool),
        LocationRepository(pool),
    ).create_movement(
        [
            MovementCreate(
                movement_type="transfer",
                product_id="testwild",
                from_location_code="PUSHKINO-ПРИЁМКА",
                to_location_code="PUSHKINO-TEST",
                quantity=3,
                batch_number=None,
                container_code=None,
            )
        ]
    )

    assert movement_result.total == 1
    async with pool.acquire() as connection:
        source = await connection.fetchval(
            """
            SELECT coalesce(sum(i.quantity), 0)
            FROM wms.inventory i JOIN wms.locations l USING (location_id)
            WHERE l.location_code='PUSHKINO-ПРИЁМКА' AND i.product_id='testwild'
            """
        )
        destination = await connection.fetchval(
            """
            SELECT coalesce(sum(i.quantity), 0)
            FROM wms.inventory i JOIN wms.locations l USING (location_id)
            WHERE l.location_code='PUSHKINO-TEST' AND i.product_id='testwild'
            """
        )
        holders = await connection.fetch(
            "SELECT lifecycle_status, location_id, container_id FROM wms.kiz ORDER BY kiz_id"
        )
    assert source == 0
    assert destination == 3
    assert [dict(row) for row in holders] == [
        {"lifecycle_status": "registered", "location_id": None, "container_id": None},
        {"lifecycle_status": "registered", "location_id": None, "container_id": None},
    ]


@pytest.mark.asyncio
async def test_active_kiz_still_blocks_legacy_full_loose_transfer(pool):
    location_id = await prepare_receipt(pool, {"testwild": 3})
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO wms.locations (location_code) VALUES ('PUSHKINO-TEST')"
        )
        await connection.execute(
            """
            INSERT INTO wms.kiz (
                kiz_code, product_id, location_id, origin_type, created_by
            ) VALUES ('ACTIVE', 'testwild', $1, 'warehouse_assignment', 'test')
            """,
            location_id,
        )

    with pytest.raises(KizConflictError, match="Недостаточно неидентифицированного остатка"):
        await MovementService(
            MovementRepository(pool),
            LocationRepository(pool),
        ).create_movement(
            [
                MovementCreate(
                    movement_type="transfer",
                    product_id="testwild",
                    from_location_code="PUSHKINO-ПРИЁМКА",
                    to_location_code="PUSHKINO-TEST",
                    quantity=3,
                    batch_number=None,
                    container_code=None,
                )
            ]
        )

    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM wms.movements") == 0
        assert (
            await connection.fetchval(
                "SELECT quantity FROM wms.inventory WHERE location_id=$1 AND product_id='testwild'",
                location_id,
            )
            == 3
        )


@pytest.mark.asyncio
async def test_terminal_same_receipt_is_linked_without_reactivation(pool):
    location_id = await prepare_receipt(pool, {"testwild": 2})
    async with pool.acquire() as connection:
        terminal_id = await connection.fetchval(
            """
            INSERT INTO wms.kiz (
                kiz_code, product_id, location_id, lifecycle_status, origin_type,
                origin_reference, closed_at, created_by
            )
            VALUES (
                'TERMINAL', 'testwild', $1, 'deactivated', 'receipt_import',
                'B2-ORDER-001', now(), 'test'
            )
            RETURNING kiz_id
            """,
            location_id,
        )
    message_id = await ingest_message(
        pool, [{"wild": "testwild", "mark_codes": ["TERMINAL", "NEW"]}]
    )

    result = await KizImportService(KizImportRepository(pool)).process_message(
        message_id, "PUSHKINO-ПРИЁМКА"
    )

    assert result["new_kiz_count"] == 1
    assert result["terminal_existing_count"] == 1
    async with pool.acquire() as connection:
        terminal = await connection.fetchrow(
            "SELECT lifecycle_status, closed_at FROM wms.kiz WHERE kiz_id=$1",
            terminal_id,
        )
        assert terminal["lifecycle_status"] == "deactivated"
        assert terminal["closed_at"] is not None
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz_events WHERE kiz_id=$1", terminal_id
            )
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("stock_kind", ["batch", "container"])
async def test_batch_or_container_only_stock_does_not_block_registration(pool, stock_kind):
    await prepare_receipt(pool, {"testwild": 2})
    async with pool.acquire() as connection:
        await connection.execute("DELETE FROM wms.inventory")
        location_id = await connection.fetchval(
            "SELECT location_id FROM wms.locations WHERE location_code='PUSHKINO-ПРИЁМКА'"
        )
        await connection.execute(
            """
            INSERT INTO wms.inventory (
                product_id, location_id, quantity, status, batch_number, container_code
            )
            VALUES ('testwild', $1, 2, 'available', $2, $3)
            """,
            location_id,
            "BATCH-1" if stock_kind == "batch" else None,
            "BOX-1" if stock_kind == "container" else None,
        )
    message_id = await ingest_message(pool, [{"wild": "testwild", "mark_codes": ["K1"]}])

    result = await KizImportService(KizImportRepository(pool)).process_message(
        message_id, "PUSHKINO-ПРИЁМКА"
    )

    assert result["new_kiz_count"] == 1
    assert result["groups"][0]["physical_quantity"] == "0"
    assert result["groups"][0]["identified_quantity"] == 0


@pytest.mark.asyncio
async def test_concurrent_assignment_does_not_consume_registration_capacity(pool):
    location_id = await prepare_receipt(pool, {"testwild": 1})
    message_id = await ingest_message(pool, [{"wild": "testwild", "mark_codes": ["RECEIPT-KIZ"]}])
    assignment_locked = asyncio.Event()
    release_assignment = asyncio.Event()

    async def assign_last_unit():
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.fetchrow(
                    """
                    SELECT inventory_id FROM wms.inventory
                    WHERE product_id='testwild' AND location_id=$1
                    FOR UPDATE
                    """,
                    location_id,
                )
                assignment_locked.set()
                await release_assignment.wait()
                await connection.execute(
                    """
                    INSERT INTO wms.kiz (
                        kiz_code, product_id, location_id, origin_type, created_by
                    )
                    VALUES (
                        'ASSIGNMENT-KIZ', 'testwild', $1,
                        'warehouse_assignment', 'test'
                    )
                    """,
                    location_id,
                )

    assignment_task = asyncio.create_task(assign_last_unit())
    await assignment_locked.wait()
    process_task = asyncio.create_task(
        KizImportService(KizImportRepository(pool)).process_message(message_id, "PUSHKINO-ПРИЁМКА")
    )
    await asyncio.sleep(0.05)
    release_assignment.set()
    await assignment_task

    result = await process_task

    assert result["new_kiz_count"] == 1
    assert result["groups"][0]["identified_quantity"] == 1
    assert result["groups"][0]["unidentified_quantity"] == "0"
    async with pool.acquire() as connection:
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz WHERE lifecycle_status='active'"
            )
            == 1
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz WHERE lifecycle_status='registered'"
            )
            == 1
        )


@pytest.mark.asyncio
async def test_rejected_message_can_be_retried_after_receipt_appears(pool):
    async with pool.acquire() as connection:
        location_id = await connection.fetchval(
            """
            INSERT INTO wms.locations (location_code)
            VALUES ('PUSHKINO-ПРИЁМКА')
            RETURNING location_id
            """
        )
        await connection.execute("INSERT INTO public.products (id) VALUES ('testwild')")
    message_id = await ingest_message(pool, [{"wild": "testwild", "mark_codes": ["LATE-KIZ"]}])
    service = KizImportService(KizImportRepository(pool))

    with pytest.raises(KizImportBusinessError) as caught:
        await service.process_message(message_id, "PUSHKINO-ПРИЁМКА")
    assert caught.value.error_code == "UNKNOWN_RECEIPT"

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO wms.receipt_items (guid, product_id, quantity)
            VALUES ('B2-ORDER-001', 'testwild', 1)
            """
        )
        await connection.execute(
            """
            INSERT INTO wms.inventory (
                product_id, location_id, quantity, status, batch_number, container_code
            )
            VALUES ('testwild', $1, 1, 'available', NULL, NULL)
            """,
            location_id,
        )

    result = await service.process_message(message_id, "PUSHKINO-ПРИЁМКА")

    assert result["business_status"] == "applied"
    assert result["new_kiz_count"] == 1
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT business_status, business_error_code, business_result
            FROM wms.kiz_import_messages
            WHERE message_id=$1
            """,
            message_id,
        )
        assert row["business_status"] == "applied"
        assert row["business_error_code"] is None
        assert row["business_result"] is not None
