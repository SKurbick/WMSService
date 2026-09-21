"""Opt-in real PostgreSQL fixtures, isolated from application DB settings."""
import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def kiz_database_template():
    dsn = os.getenv("KIZ_TEST_DSN")
    if not dsn:
        pytest.skip("Set KIZ_TEST_DSN to a disposable local PostgreSQL database named kiz_test")
    url = urlparse(dsn)
    if url.hostname not in {"localhost", "127.0.0.1", "::1"} or url.path != "/kiz_test":
        pytest.fail("KIZ_TEST_DSN must point to a local disposable database named kiz_test")
    template = "kiz_template_" + uuid4().hex

    async def build():
        admin = await asyncpg.connect(dsn)
        try:
            await admin.execute(f"CREATE DATABASE {template}")
            conn = await asyncpg.connect(dsn, database=template)
            try:
                snapshot = ROOT / "docs/archive/runtime/2026-08-08-production"
                public = (snapshot / "runtime_public_schema.sql").read_text()
                # Snapshot omits public.article. This unrelated external FK is
                # excluded explicitly; all WMS tables/functions/triggers are restored.
                public = re.sub(
                    r"ALTER TABLE ONLY public.assembly_task\s+ADD CONSTRAINT assembly_task_article_id_fkey[^;]+;",
                    "",
                    public,
                )
                # Two public product trigger functions are outside this schema-only dump.
                public = re.sub(
                    r"CREATE TRIGGER trg_(?:after_insert_products|products_updated_at)[^;]+;",
                    "",
                    public,
                )
                wms = (snapshot / "runtime_wms_schema.sql").read_text()
                wms = wms.replace(
                    "CREATE SCHEMA wms;",
                    "CREATE SCHEMA wms; CREATE EXTENSION ltree WITH SCHEMA wms;",
                )
                for sql in (public, wms):
                    sql = "\n".join(
                        line
                        for line in sql.splitlines()
                        if not line.startswith("\\") and line != "SET transaction_timeout = 0;"
                    )
                    await conn.execute(sql)
                await conn.execute(
                    (ROOT / "scripts/migrations/20260906_add_kiz_v1.sql").read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260910_add_movement_registry.sql").read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260910_kiz_phase2_preflight.sql").read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260910_add_kiz_movement_links.sql").read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260910_kiz_phase3_preflight.sql").read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260910_add_kiz_operations.sql").read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260910_kiz_phase4_preflight.sql").read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260910_add_kiz_transfer.sql").read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260911_kiz_phase5_preflight.sql").read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260911_add_kiz_ship.sql").read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260915_container_b1_preflight.sql").read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260915_add_container_b1_contract.sql").read_text()
                )
                await conn.execute(
                    (
                        ROOT / "scripts/migrations/20260915_container_b21_fill_preflight.sql"
                    ).read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260915_add_container_b21_fill.sql").read_text()
                )
                await conn.execute(
                    (
                        ROOT / "scripts/migrations/20260916_container_b22_extract_preflight.sql"
                    ).read_text()
                )
                await conn.execute(
                    (ROOT / "scripts/migrations/20260916_add_container_b22_extract.sql").read_text()
                )
                await conn.execute(
                    (
                        ROOT
                        / "scripts/migrations/20260916_container_b3_move_unpack_all_preflight.sql"
                    ).read_text()
                )
                await conn.execute(
                    (
                        ROOT / "scripts/migrations/20260916_add_container_b3_move_unpack_all.sql"
                    ).read_text()
                )
                await conn.execute(
                    (
                        ROOT / "scripts/migrations/20260917_container_b4_final_preflight.sql"
                    ).read_text()
                )
                await conn.execute(
                    (
                        ROOT / "scripts/migrations/20260917_add_container_b4_final.sql"
                    ).read_text()
                )
                await conn.execute(
                    (
                        ROOT / "scripts/migrations/20260918_container_c1_kiz_preflight.sql"
                    ).read_text()
                )
                await conn.execute(
                    (
                        ROOT / "scripts/migrations/20260918_add_container_c1_kiz.sql"
                    ).read_text()
                )
                # Existing manual HTTP FBS prerequisite, not part of KIZ migration.
                await conn.execute(
                    """
                    ALTER TABLE wms.fbs_shipments DROP CONSTRAINT chk_fbs_shipments_source;
                    ALTER TABLE wms.fbs_shipments ADD CONSTRAINT chk_fbs_shipments_source
                      CHECK (source IN ('standard','external_detected','http_api'));
                """
                )
            finally:
                await conn.close()
        finally:
            await admin.close()

    async def cleanup():
        admin = await asyncpg.connect(dsn)
        try:
            await admin.execute(f"DROP DATABASE IF EXISTS {template} WITH (FORCE)")
        finally:
            await admin.close()

    try:
        asyncio.run(build())
        yield dsn, template
    finally:
        asyncio.run(cleanup())


@pytest.fixture
async def kiz_pool(kiz_database_template):
    dsn, template = kiz_database_template
    name = "kiz_case_" + uuid4().hex
    admin = await asyncpg.connect(dsn)
    pool = None
    try:
        await admin.execute(f"CREATE DATABASE {name} TEMPLATE {template}")
        pool = await asyncpg.create_pool(
            dsn,
            database=name,
            min_size=1,
            max_size=6,
            command_timeout=10,
            server_settings={"search_path": "wms,public"},
        )
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        await admin.close()
