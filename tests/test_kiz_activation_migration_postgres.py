import os
from pathlib import Path

import asyncpg
import pytest


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("KIZ_ACTIVATION_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="KIZ_ACTIVATION_TEST_DATABASE_URL is required for disposable PostgreSQL tests",
)


async def reset_b21_schema():
    connection = await asyncpg.connect(DATABASE_URL)
    try:
        await connection.execute("DROP SCHEMA IF EXISTS wms CASCADE")
        await connection.execute("DROP TABLE IF EXISTS public.products")
        for relative_path in (
            "tests/fixtures/kiz_b2_base_schema.sql",
            "scripts/migrations/20260923_add_kiz_import_inbox.sql",
            "scripts/migrations/20260924_add_kiz_import_b2.sql",
            "scripts/migrations/20260928_kiz_receipt_b21_registered.sql",
        ):
            await connection.execute((ROOT / relative_path).read_text())
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_activation_migration_preserves_existing_registered_rows():
    await reset_b21_schema()
    connection = await asyncpg.connect(DATABASE_URL)
    try:
        await connection.execute("INSERT INTO public.products (id) VALUES ('p1')")
        kiz_id = await connection.fetchval(
            """
            INSERT INTO wms.kiz (
                kiz_code, product_id, lifecycle_status, origin_type,
                origin_reference, created_by, metadata
            ) VALUES (
                'REGISTERED-1', 'p1', 'registered', 'receipt_import',
                'ORDER-1', 'test', '{"source":"migration-test"}'::jsonb
            )
            RETURNING kiz_id
            """
        )
        before = await connection.fetchrow(
            "SELECT * FROM wms.kiz WHERE kiz_id = $1",
            kiz_id,
        )
        existing_operation_id = await connection.fetchval(
            """
            INSERT INTO wms.kiz_operations (
                operation_type, source_system, external_operation_id,
                request_fingerprint, author, result_payload
            ) VALUES (
                'transfer', 'migration-test', 'existing-transfer',
                repeat('a', 64), 'test', '{"movement_ids":[]}'::jsonb
            )
            RETURNING operation_id
            """
        )

        await connection.execute(
            (ROOT / "scripts/migrations/20261001_kiz_activation_preflight.sql").read_text()
        )
        await connection.execute(
            (ROOT / "scripts/migrations/20261001_add_kiz_activation.sql").read_text()
        )

        after = await connection.fetchrow(
            "SELECT * FROM wms.kiz WHERE kiz_id = $1",
            kiz_id,
        )
        event_constraint = await connection.fetchval(
            """
            SELECT pg_get_constraintdef(oid, true)
            FROM pg_constraint
            WHERE conrelid = 'wms.kiz_events'::regclass
              AND conname = 'chk_kiz_events_event_type'
            """
        )
        guard_definition = await connection.fetchval(
            "SELECT pg_get_functiondef('wms.guard_kiz_identity()'::regprocedure)"
        )

        assert dict(after) == dict(before)
        assert (
            await connection.fetchval(
                "SELECT operation_type FROM wms.kiz_operations WHERE operation_id = $1",
                existing_operation_id,
            )
            == "transfer"
        )
        assert await connection.fetchval("SELECT to_regclass('wms.kiz_activation_authorizations')")
        assert "activated" in event_constraint
        assert "kiz_activation_authorizations" in guard_definition
        assert "OLD.lifecycle_status = 'registered'" in guard_definition
    finally:
        await connection.close()
