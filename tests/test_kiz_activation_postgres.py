import asyncio
import json
import os
from pathlib import Path

import asyncpg
import pytest

from app.core.exceptions import KizActivationNotFoundError, KizConflictError
from app.core.schemas.kiz_activation import KizActivationRequest
from app.core.schemas.movement import MovementCreate
from app.core.services.kiz_activation_service import KizActivationService
from app.core.services.movement_service import MovementService
from app.infrastructure.database.repositories.kiz_activation_repository import (
    KizActivationRepository,
)
from app.infrastructure.database.repositories.location_repository import LocationRepository
from app.infrastructure.database.repositories.movement_repository import MovementRepository


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("KIZ_ACTIVATION_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="KIZ_ACTIVATION_TEST_DATABASE_URL is required for disposable PostgreSQL tests",
)


@pytest.fixture
async def pool():
    connection = await asyncpg.connect(DATABASE_URL)
    try:
        await connection.execute("DROP SCHEMA IF EXISTS wms CASCADE")
        await connection.execute("DROP TABLE IF EXISTS public.products")
        for relative_path in (
            "tests/fixtures/kiz_b2_base_schema.sql",
            "scripts/migrations/20260923_add_kiz_import_inbox.sql",
            "scripts/migrations/20260924_add_kiz_import_b2.sql",
            "scripts/migrations/20260928_kiz_receipt_b21_registered.sql",
            "scripts/migrations/20261001_add_kiz_activation.sql",
        ):
            await connection.execute((ROOT / relative_path).read_text())
    finally:
        await connection.close()

    test_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    yield test_pool
    await test_pool.close()


@pytest.fixture(autouse=True)
async def clean_database(pool):
    async with pool.acquire() as connection:
        await connection.execute(
            """
            TRUNCATE
                wms.kiz_operation_items,
                wms.kiz_operations,
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


def activation_request(codes, *, key="activation-1", location="LOC-A", user="operator"):
    return KizActivationRequest(
        kiz_codes=codes,
        location_code=location,
        user_name=user,
        idempotency_key=key,
    )


async def prepare_product(
    pool,
    product_id,
    *,
    physical,
    location_code="LOC-A",
    destination_code=None,
):
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO public.products (id) VALUES ($1) ON CONFLICT DO NOTHING",
            product_id,
        )
        location_id = await connection.fetchval(
            """
            INSERT INTO wms.locations (location_code)
            VALUES ($1)
            ON CONFLICT (location_code) DO UPDATE SET location_code = EXCLUDED.location_code
            RETURNING location_id
            """,
            location_code,
        )
        if physical:
            await connection.execute(
                """
                INSERT INTO wms.inventory (
                    product_id, location_id, quantity, status, batch_number, container_code
                ) VALUES ($1, $2, $3, 'available', NULL, NULL)
                """,
                product_id,
                location_id,
                physical,
            )
        if destination_code:
            await connection.execute(
                """
                INSERT INTO wms.locations (location_code)
                VALUES ($1) ON CONFLICT DO NOTHING
                """,
                destination_code,
            )
    return location_id


async def insert_kiz(
    pool,
    code,
    product_id,
    *,
    lifecycle="registered",
    location_id=None,
    container_id=None,
):
    async with pool.acquire() as connection:
        return await connection.fetchval(
            """
            INSERT INTO wms.kiz (
                kiz_code, product_id, location_id, container_id,
                lifecycle_status, origin_type, origin_reference,
                closed_at, created_by
            ) VALUES (
                $1, $2, $3, $4, $5::varchar, 'receipt_import', 'ORDER-1',
                CASE WHEN $5::varchar IN ('error', 'deactivated', 'shipped') THEN now() END,
                'test'
            )
            RETURNING kiz_id
            """,
            code,
            product_id,
            location_id,
            container_id,
            lifecycle,
        )


async def fetch_kiz(pool, codes):
    async with pool.acquire() as connection:
        return await connection.fetch(
            """
            SELECT kiz_code, product_id, lifecycle_status, location_id, container_id
            FROM wms.kiz
            WHERE kiz_code = ANY($1::text[])
            ORDER BY kiz_code
            """,
            codes,
        )


async def counts(pool, product_id, location_id):
    async with pool.acquire() as connection:
        return await connection.fetchrow(
            """
            SELECT
                COALESCE((
                    SELECT quantity FROM wms.inventory
                    WHERE product_id = $1 AND location_id = $2
                      AND status = 'available'
                      AND batch_number IS NULL AND container_code IS NULL
                ), 0) AS physical,
                (SELECT count(*) FROM wms.kiz
                 WHERE product_id = $1 AND location_id = $2
                   AND container_id IS NULL AND lifecycle_status = 'active') AS identified,
                (SELECT count(*) FROM wms.movements WHERE product_id = $1) AS movements
            """,
            product_id,
            location_id,
        )


@pytest.mark.asyncio
async def test_registered_activation_changes_identification_without_physical_movement(pool):
    location_id = await prepare_product(pool, "p1", physical=3)
    await insert_kiz(pool, "K1", "p1")
    await insert_kiz(pool, "K2", "p1")
    service = KizActivationService(KizActivationRepository(pool))

    first = await service.activate(activation_request(["K1"], key="first"))
    after_first = await counts(pool, "p1", location_id)
    second = await service.activate(activation_request(["K2"], key="second"))
    after_second = await counts(pool, "p1", location_id)

    assert first["activated_count"] == 1
    assert second["activated_count"] == 1
    assert dict(after_first) == {"physical": 3, "identified": 1, "movements": 0}
    assert dict(after_second) == {"physical": 3, "identified": 2, "movements": 0}
    rows = await fetch_kiz(pool, ["K1", "K2"])
    assert all(row["lifecycle_status"] == "active" for row in rows)
    assert all(row["location_id"] == location_id for row in rows)
    assert all(row["container_id"] is None for row in rows)


@pytest.mark.asyncio
async def test_same_idempotency_replays_saved_result_without_duplicate_events(pool):
    await prepare_product(pool, "p1", physical=2)
    await insert_kiz(pool, "K1", "p1")
    service = KizActivationService(KizActivationRepository(pool))
    request = activation_request(["K1"], key="same-key")

    first = await service.activate(request)
    replay = await service.activate(request)

    assert replay == first
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz_operations") == 1
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz_events WHERE event_type = 'activated'"
            )
            == 1
        )


@pytest.mark.asyncio
async def test_active_same_location_with_new_key_is_already_active_no_event(pool):
    location_id = await prepare_product(pool, "p1", physical=2)
    await insert_kiz(pool, "K1", "p1", lifecycle="active", location_id=location_id)
    service = KizActivationService(KizActivationRepository(pool))

    result = await service.activate(activation_request(["K1"], key="new-key"))

    assert result["activated_count"] == 0
    assert result["already_active_count"] == 1
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz_events") == 0
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz_operation_items") == 1


@pytest.mark.asyncio
async def test_same_key_different_intent_conflicts(pool):
    await prepare_product(pool, "p1", physical=2)
    await insert_kiz(pool, "K1", "p1")
    await insert_kiz(pool, "K2", "p1")
    service = KizActivationService(KizActivationRepository(pool))
    await service.activate(activation_request(["K1"], key="same-key"))

    with pytest.raises(KizConflictError):
        await service.activate(activation_request(["K2"], key="same-key"))


@pytest.mark.asyncio
async def test_insufficient_capacity_rolls_back_whole_batch(pool):
    await prepare_product(pool, "p1", physical=1)
    await insert_kiz(pool, "K1", "p1")
    await insert_kiz(pool, "K2", "p1")
    service = KizActivationService(KizActivationRepository(pool))

    with pytest.raises(KizConflictError):
        await service.activate(activation_request(["K1", "K2"]))

    assert [row["lifecycle_status"] for row in await fetch_kiz(pool, ["K1", "K2"])] == [
        "registered",
        "registered",
    ]
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz_operations") == 0
        assert await connection.fetchval("SELECT count(*) FROM wms.kiz_events") == 0


@pytest.mark.asyncio
async def test_mixed_products_activate_with_independent_capacity(pool):
    location_id = await prepare_product(pool, "p1", physical=1)
    await prepare_product(pool, "p2", physical=2)
    await insert_kiz(pool, "K1", "p1")
    await insert_kiz(pool, "K2", "p2")
    service = KizActivationService(KizActivationRepository(pool))

    result = await service.activate(activation_request(["K2", "K1"]))

    assert result["activated_count"] == 2
    assert [item["kiz_code"] for item in result["items"]] == ["K1", "K2"]
    assert (await counts(pool, "p1", location_id))["identified"] == 1
    assert (await counts(pool, "p2", location_id))["identified"] == 1


@pytest.mark.asyncio
async def test_mixed_products_capacity_failure_is_atomic(pool):
    await prepare_product(pool, "p1", physical=1)
    await prepare_product(pool, "p2", physical=0)
    await insert_kiz(pool, "K1", "p1")
    await insert_kiz(pool, "K2", "p2")
    service = KizActivationService(KizActivationRepository(pool))

    with pytest.raises(KizConflictError):
        await service.activate(activation_request(["K1", "K2"]))

    assert [row["lifecycle_status"] for row in await fetch_kiz(pool, ["K1", "K2"])] == [
        "registered",
        "registered",
    ]


@pytest.mark.asyncio
async def test_unknown_kiz_rolls_back_existing_kiz(pool):
    await prepare_product(pool, "p1", physical=2)
    await insert_kiz(pool, "K1", "p1")
    service = KizActivationService(KizActivationRepository(pool))

    with pytest.raises(KizActivationNotFoundError) as error:
        await service.activate(activation_request(["K1", "UNKNOWN"]))

    assert error.value.error_code == "KIZ_NOT_FOUND"
    assert (await fetch_kiz(pool, ["K1"]))[0]["lifecycle_status"] == "registered"


@pytest.mark.asyncio
async def test_unknown_and_inactive_locations_are_controlled(pool):
    await prepare_product(pool, "p1", physical=1)
    await insert_kiz(pool, "K1", "p1")
    service = KizActivationService(KizActivationRepository(pool))

    with pytest.raises(KizActivationNotFoundError) as error:
        await service.activate(activation_request(["K1"], location="MISSING"))
    assert error.value.error_code == "LOCATION_NOT_FOUND"

    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO wms.locations (location_code, is_active) VALUES ('INACTIVE', false)"
        )
    with pytest.raises(KizConflictError):
        await service.activate(activation_request(["K1"], location="INACTIVE", key="inactive"))


@pytest.mark.asyncio
async def test_active_other_location_and_container_holder_conflict(pool):
    location_id = await prepare_product(pool, "p1", physical=3)
    other_id = await prepare_product(pool, "p1", physical=3, location_code="LOC-B")
    await insert_kiz(pool, "OTHER", "p1", lifecycle="active", location_id=other_id)
    await insert_kiz(pool, "CONTAINED", "p1", lifecycle="active", container_id=99)
    service = KizActivationService(KizActivationRepository(pool))

    with pytest.raises(KizConflictError):
        await service.activate(activation_request(["OTHER"], key="other"))
    with pytest.raises(KizConflictError):
        await service.activate(activation_request(["CONTAINED"], key="contained"))
    assert location_id != other_id


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", ["shipped", "deactivated", "error"])
async def test_terminal_lifecycle_is_not_reactivated(pool, lifecycle):
    await prepare_product(pool, "p1", physical=2)
    await insert_kiz(pool, "K1", "p1", lifecycle=lifecycle)
    service = KizActivationService(KizActivationRepository(pool))

    with pytest.raises(KizConflictError):
        await service.activate(activation_request(["K1"], key=f"terminal-{lifecycle}"))
    assert (await fetch_kiz(pool, ["K1"]))[0]["lifecycle_status"] == lifecycle


@pytest.mark.asyncio
async def test_concurrent_activation_same_kiz_creates_one_event(pool):
    await prepare_product(pool, "p1", physical=1)
    await insert_kiz(pool, "K1", "p1")
    service = KizActivationService(KizActivationRepository(pool))

    results = await asyncio.gather(
        service.activate(activation_request(["K1"], key="concurrent-a")),
        service.activate(activation_request(["K1"], key="concurrent-b")),
    )

    assert sorted(result["activated_count"] for result in results) == [0, 1]
    assert sorted(result["already_active_count"] for result in results) == [0, 1]
    async with pool.acquire() as connection:
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz_events WHERE event_type = 'activated'"
            )
            == 1
        )


@pytest.mark.asyncio
async def test_concurrent_last_unidentified_unit_allows_exactly_one(pool):
    location_id = await prepare_product(pool, "p1", physical=1)
    await insert_kiz(pool, "K1", "p1")
    await insert_kiz(pool, "K2", "p1")
    service = KizActivationService(KizActivationRepository(pool))

    results = await asyncio.gather(
        service.activate(activation_request(["K1"], key="capacity-a")),
        service.activate(activation_request(["K2"], key="capacity-b")),
        return_exceptions=True,
    )

    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, KizConflictError) for result in results) == 1
    state = await counts(pool, "p1", location_id)
    assert state["identified"] == 1
    assert state["physical"] == 1


@pytest.mark.asyncio
async def test_legacy_movement_guard_blocks_full_but_allows_unidentified_quantity(pool):
    location_id = await prepare_product(
        pool,
        "p1",
        physical=3,
        destination_code="LOC-B",
    )
    await insert_kiz(pool, "K1", "p1")
    activation = KizActivationService(KizActivationRepository(pool))
    await activation.activate(activation_request(["K1"]))
    movement = MovementService(MovementRepository(pool), LocationRepository(pool))

    with pytest.raises(KizConflictError):
        await movement.create_movement(
            [
                MovementCreate(
                    movement_type="transfer",
                    product_id="p1",
                    from_location_code="LOC-A",
                    to_location_code="LOC-B",
                    quantity=3,
                )
            ]
        )

    result = await movement.create_movement(
        [
            MovementCreate(
                movement_type="transfer",
                product_id="p1",
                from_location_code="LOC-A",
                to_location_code="LOC-B",
                quantity=2,
            )
        ]
    )
    assert result.total == 1
    state = await counts(pool, "p1", location_id)
    assert state["physical"] == 1
    assert state["identified"] == 1
    assert state["movements"] == 1


@pytest.mark.asyncio
async def test_activated_history_has_operation_metadata_and_no_movement(pool):
    location_id = await prepare_product(pool, "p1", physical=1)
    kiz_id = await insert_kiz(pool, "K1", "p1")
    service = KizActivationService(KizActivationRepository(pool))
    result = await service.activate(activation_request(["K1"], key="history"))

    async with pool.acquire() as connection:
        event = await connection.fetchrow(
            """
            SELECT event_type, from_status, to_status, location_id,
                   container_id, movement_ref, author, metadata
            FROM wms.kiz_events
            WHERE kiz_id = $1
            """,
            kiz_id,
        )
    assert event["event_type"] == "activated"
    assert event["from_status"] == "registered"
    assert event["to_status"] == "active"
    assert event["location_id"] == location_id
    assert event["container_id"] is None
    assert event["movement_ref"] is None
    assert json.loads(event["metadata"])["operation_id"] == result["operation_id"]


@pytest.mark.asyncio
async def test_direct_registered_to_active_update_without_operation_is_blocked(pool):
    location_id = await prepare_product(pool, "p1", physical=1)
    kiz_id = await insert_kiz(pool, "K1", "p1")

    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.PostgresError) as error:
            await connection.execute(
                """
                UPDATE wms.kiz
                SET lifecycle_status = 'active', location_id = $2
                WHERE kiz_id = $1
                """,
                kiz_id,
                location_id,
            )
    assert error.value.sqlstate == "P7501"
    assert (await fetch_kiz(pool, ["K1"]))[0]["lifecycle_status"] == "registered"
