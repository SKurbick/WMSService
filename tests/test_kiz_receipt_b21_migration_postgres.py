import os
from pathlib import Path

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("KIZ_B2_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="KIZ_B2_TEST_DATABASE_URL is required for disposable PostgreSQL tests",
)


async def reset_pre_b21_schema():
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
    finally:
        await connection.close()


async def insert_legacy_receipt_kiz(connection, *, product_id="testwild"):
    await connection.execute("INSERT INTO public.products (id) VALUES ($1)", product_id)
    location_id = await connection.fetchval(
        """
        INSERT INTO wms.locations (location_code)
        VALUES ('PUSHKINO-ПРИЁМКА')
        RETURNING location_id
        """
    )
    await connection.execute(
        """
        INSERT INTO wms.inventory (
            product_id, location_id, quantity, status, batch_number, container_code
        ) VALUES ($1, $2, 3, 'available', NULL, NULL)
        """,
        product_id,
        location_id,
    )
    message_id = await connection.fetchval(
        """
        INSERT INTO wms.kiz_import_messages (
            raw_body, raw_payload, parse_status, order_guid,
            wild_group_count, mark_code_count
        ) VALUES ('{}', '{}'::jsonb, 'received', 'ORDER-1', 1, 1)
        RETURNING message_id
        """
    )
    kiz_id = await connection.fetchval(
        """
        INSERT INTO wms.kiz (
            kiz_code, product_id, location_id, lifecycle_status,
            origin_type, origin_reference, created_by
        ) VALUES ('LEGACY', $1, $2, 'active', 'receipt_import', 'ORDER-1', 'test')
        RETURNING kiz_id
        """,
        product_id,
        location_id,
    )
    await connection.execute(
        """
        INSERT INTO wms.kiz_events (
            kiz_id, event_type, from_status, to_status,
            product_id, location_id, author, metadata
        ) VALUES ($1, 'assigned', NULL, 'active', $2, $3, 'test', '{}'::jsonb)
        """,
        kiz_id,
        product_id,
        location_id,
    )
    await connection.execute(
        """
        INSERT INTO wms.kiz_import_message_kiz (message_id, kiz_id, was_created)
        VALUES ($1, $2, true)
        """,
        message_id,
        kiz_id,
    )
    return kiz_id, location_id


@pytest.mark.asyncio
async def test_b21_migration_backfills_safe_receipt_import_without_physical_changes():
    await reset_pre_b21_schema()
    connection = await asyncpg.connect(DATABASE_URL)
    try:
        kiz_id, location_id = await insert_legacy_receipt_kiz(connection)
        before_event = await connection.fetchrow(
            "SELECT * FROM wms.kiz_events WHERE kiz_id=$1", kiz_id
        )
        await connection.execute(
            (ROOT / "scripts/migrations/20260928_kiz_receipt_b21_registered.sql").read_text()
        )

        kiz = await connection.fetchrow(
            """
            SELECT lifecycle_status, location_id, container_id, closed_at,
                   origin_type, origin_reference
            FROM wms.kiz WHERE kiz_id=$1
            """,
            kiz_id,
        )
        after_event = await connection.fetchrow(
            "SELECT * FROM wms.kiz_events WHERE kiz_id=$1", kiz_id
        )
        assert dict(kiz) == {
            "lifecycle_status": "registered",
            "location_id": None,
            "container_id": None,
            "closed_at": None,
            "origin_type": "receipt_import",
            "origin_reference": "ORDER-1",
        }
        assert dict(after_event) == dict(before_event)
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM wms.kiz_import_message_kiz WHERE kiz_id=$1", kiz_id
            )
            == 1
        )
        assert (
            await connection.fetchval(
                "SELECT quantity FROM wms.inventory WHERE location_id=$1", location_id
            )
            == 3
        )
    finally:
        await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_kind",
    ["non_test_product", "movement_link", "contained", "shipped_history"],
)
async def test_b21_migration_stops_and_rolls_back_for_unsafe_rows(unsafe_kind):
    await reset_pre_b21_schema()
    connection = await asyncpg.connect(DATABASE_URL)
    try:
        product_id = "wild-production" if unsafe_kind == "non_test_product" else "testwild"
        kiz_id, _ = await insert_legacy_receipt_kiz(connection, product_id=product_id)
        if unsafe_kind == "movement_link":
            await connection.execute(
                "INSERT INTO wms.kiz_movement_links (kiz_id, movement_ref) VALUES ($1, 10)",
                kiz_id,
            )
        elif unsafe_kind == "contained":
            await connection.execute("ALTER TABLE wms.kiz DISABLE TRIGGER trg_kiz_identity_guard")
            await connection.execute(
                "UPDATE wms.kiz SET location_id=NULL, container_id=10 WHERE kiz_id=$1",
                kiz_id,
            )
            await connection.execute("ALTER TABLE wms.kiz ENABLE TRIGGER trg_kiz_identity_guard")
        elif unsafe_kind == "shipped_history":
            await connection.execute(
                "INSERT INTO wms.kiz_movement_links (kiz_id, movement_ref) VALUES ($1, 10)",
                kiz_id,
            )
            await connection.execute(
                """
                INSERT INTO wms.kiz_events (
                    kiz_id, event_type, from_status, to_status, product_id,
                    location_id, author, movement_ref
                ) VALUES ($1, 'shipped', 'active', 'shipped', $2, NULL, 'test', 10)
                """,
                kiz_id,
                product_id,
            )

        with pytest.raises(asyncpg.DivisionByZeroError):
            await connection.execute(
                (ROOT / "scripts/migrations/20260928_kiz_receipt_b21_registered.sql").read_text()
            )
        await connection.execute("ROLLBACK")

        row = await connection.fetchrow(
            "SELECT lifecycle_status, origin_type FROM wms.kiz WHERE kiz_id=$1", kiz_id
        )
        assert dict(row) == {
            "lifecycle_status": "active",
            "origin_type": "receipt_import",
        }
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM pg_constraint WHERE conname='chk_kiz_lifecycle_status' "
                "AND pg_get_constraintdef(oid) LIKE '%registered%'"
            )
            == 0
        )
    finally:
        await connection.close()
