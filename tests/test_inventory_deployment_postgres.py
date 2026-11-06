"""B.1/B.2 migration and real non-owner regressions; disposable PostgreSQL only."""

import json
import os
from uuid import uuid4

import asyncpg
import pytest

from conftest import kiz_database_template, kiz_pool
from app.infrastructure.database.connection import get_db_pool
from app.shared.config import settings
from test_inventory_documents_postgres import ROOT, inventory_env
from test_inventory_freeze_postgres import freeze_env  # noqa: F401
from test_inventory_physical_apply_postgres import physical_env  # noqa: F401
import test_inventory_freeze_postgres as freezes
import test_inventory_physical_apply_postgres as physical

MIGRATIONS = ROOT / "scripts/migrations"
FREEZE = (MIGRATIONS / "20261030_add_inventory_location_freeze.sql").read_text()
APPLY = (MIGRATIONS / "20261030_add_inventory_physical_apply.sql").read_text()
PROJECTION_PREFLIGHT = (MIGRATIONS / "20261102_inventory_projection_preflight.sql").read_text()
PRIVILEGES_PREFLIGHT = (
    MIGRATIONS / "20261102_inventory_runtime_privileges_preflight.sql"
).read_text()


@pytest.fixture(scope="module")
def legacy_template():
    """Build a separate unmigrated template even in the all-migrations suite."""
    previous = os.environ.pop("KIZ_TEST_INVENTORY_SCHEMA", None)
    generator = kiz_database_template.__wrapped__()
    try:
        template = next(generator)
    finally:
        if previous is not None:
            os.environ["KIZ_TEST_INVENTORY_SCHEMA"] = previous
    try:
        yield template
    finally:
        generator.close()


@pytest.fixture
async def migration_env(legacy_template, monkeypatch):
    generator = kiz_pool.__wrapped__(legacy_template, False)
    pool = await anext(generator)
    environment = inventory_env.__wrapped__(pool, monkeypatch)
    env = await anext(environment)
    try:
        yield env
    finally:
        await environment.aclose()
        await generator.aclose()


async def failed_migration(conn, sql, code):
    with pytest.raises(asyncpg.PostgresError) as caught:
        await conn.execute(sql)
    assert caught.value.sqlstate == code
    await conn.execute("ROLLBACK")


@pytest.mark.parametrize("comment_free", [False, True])
async def test_supported_projection_wrapper_executes_strict_branch(migration_env, comment_free):
    env = migration_env
    async with env["pool"].acquire() as conn:
        original = await conn.fetchval(
            "SELECT prosrc FROM pg_proc WHERE oid='wms.update_inventory_from_movement()'::regprocedure"
        )
        if comment_free:
            definition = await conn.fetchval(
                "SELECT pg_get_functiondef('wms.update_inventory_from_movement()'::regprocedure)"
            )
            plain = "\n".join(
                line for line in original.strip().splitlines() if not line.lstrip().startswith("--")
            )
            await conn.execute(definition.replace(original, plain))
        await conn.execute(PROJECTION_PREFLIGHT)
        await conn.execute(FREEZE)
        await conn.execute(APPLY)
    # Exercise executable SQL, not just source substring presence.
    await physical.test_legacy_adjust_semantics_and_unproven_inventory_source(env)
    settings_before = settings.INVENTORY_FREEZE_ENABLED, settings.INVENTORY_APPLY_ENABLED
    settings.INVENTORY_FREEZE_ENABLED = settings.INVENTORY_APPLY_ENABLED = True
    try:
        await physical.test_successful_shortage_surplus_zero_and_no_delta(env, "8", 1)
    finally:
        settings.INVENTORY_FREEZE_ENABLED, settings.INVENTORY_APPLY_ENABLED = settings_before


@pytest.mark.parametrize(
    "variant", ["leading_comment", "declaration_string", "unknown_body", "security_definer"]
)
async def test_unsupported_projection_rejects_and_rolls_back_entire_migration(
    migration_env, variant
):
    env = migration_env
    async with env["pool"].acquire() as conn:
        await conn.execute(FREEZE)
        original = await conn.fetchval(
            "SELECT prosrc FROM pg_proc WHERE oid='wms.update_inventory_from_movement()'::regprocedure"
        )
        definition = await conn.fetchval(
            "SELECT pg_get_functiondef('wms.update_inventory_from_movement()'::regprocedure)"
        )
        body = {
            "leading_comment": "/* BEGIN installed function documentation */\n" + original,
            "declaration_string": original.replace("DECLARE", "DECLARE marker text := 'BEGIN';", 1),
            "unknown_body": original.replace("RETURN NEW;", "RETURN NULL;"),
            "security_definer": original,
        }[variant]
        await conn.execute(definition.replace(original, body))
        if variant == "security_definer":
            await conn.execute(
                "ALTER FUNCTION wms.update_inventory_from_movement() SECURITY DEFINER"
            )
        before = await conn.fetchval(
            "SELECT pg_get_functiondef('wms.update_inventory_from_movement()'::regprocedure)"
        )
        await failed_migration(conn, PROJECTION_PREFLIGHT, "P7604")
        await failed_migration(conn, APPLY, "P7604")
        assert await conn.fetchval("SELECT to_regclass('wms.inventory_applications')") is None
        assert (
            await conn.fetchval(
                "SELECT to_regprocedure('wms.apply_strict_inventory_source_adjustment(wms.movements)')"
            )
            is None
        )
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='wms' "
            "AND table_name='inventory_application_components' AND column_name='target_quantity')"
        )
        assert (
            await conn.fetchval(
                "SELECT pg_get_functiondef('wms.update_inventory_from_movement()'::regprocedure)"
            )
            == before
        )
        assert await conn.fetchval("SELECT count(*) FROM wms.inventory_documents") == 0


async def grant_legacy(conn, role):
    """Existing rights only; no new inventory tables/identity sequences."""
    await conn.execute(f'GRANT USAGE ON SCHEMA wms,public TO "{role}"')
    rows = await conn.fetch(
        "SELECT c.oid::regclass AS name FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname IN ('wms','public') AND c.relkind IN ('r','p','S') "
        "AND c.relname NOT LIKE 'inventory_document%' AND c.relname NOT LIKE 'inventory_application%' "
        "AND c.relname NOT LIKE 'inventory_count_attempts%' AND c.relname NOT LIKE 'inventory_location_claims%' "
        "AND c.relname NOT LIKE 'inventory_freeze%' AND c.relname NOT LIKE 'inventory_location_fences%'"
        "AND c.relname NOT LIKE 'inventory_identity_%'"
    )
    await conn.execute(
        f'GRANT EXECUTE ON FUNCTION wms.ship_kiz(bigint,bigint,varchar,bigint,numeric) TO "{role}"'
    )
    for row in rows:
        sequence = await conn.fetchval(
            "SELECT relkind='S' FROM pg_class WHERE oid=$1::regclass", row["name"]
        )
        kind = "SEQUENCE" if sequence else "TABLE"
        await conn.execute(f'GRANT ALL ON {kind} {row["name"]} TO "{role}"')


@pytest.fixture
async def runtime_env(physical_env, request):  # noqa: F811
    env = dict(physical_env)
    role = "inventory_runtime_" + uuid4().hex
    owner_pool = env["pool"]
    profile = getattr(request, "param", "inventory")
    env["owner_pool"] = owner_pool
    runtime_pool = None
    async with owner_pool.acquire() as conn:
        await conn.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'runtime_disposable_only'")
        await grant_legacy(conn, role)
        await conn.execute(
            "SELECT set_config('wms.inventory_runtime_roles',$1,false), "
            "set_config('wms.inventory_grant_runtime_privileges','on',false)",
            json.dumps([{"role": role, "profile": profile}]),
        )
        await conn.execute("SELECT wms.inventory_runtime_privileges('apply')")
        if await conn.fetchval(
            "SELECT to_regprocedure('wms.inventory_identity_runtime_privileges()') IS NOT NULL"
        ):
            await conn.execute("SELECT wms.inventory_identity_runtime_privileges()")
        await conn.execute(PRIVILEGES_PREFLIGHT)
        database = await conn.fetchval("SELECT current_database()")
    try:
        runtime_pool = await asyncpg.create_pool(
            os.environ["KIZ_TEST_DSN"],
            database=database,
            user=role,
            password="runtime_disposable_only",
            min_size=1,
            max_size=6,
            server_settings={"search_path": "wms,public"},
        )
        async with runtime_pool.acquire() as conn:
            assert await conn.fetchval("SELECT current_user") == role
            assert not await conn.fetchval(
                "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
            )
            assert not await conn.fetchval(
                "SELECT has_table_privilege(current_user,'wms.inventory_document_history','UPDATE')"
            )
            assert not await conn.fetchval(
                "SELECT has_function_privilege(current_user,'wms.inventory_runtime_privileges(text)','EXECUTE')"
            )
        env["pool"] = runtime_pool
        app = env["client"]._transport.app
        app.dependency_overrides[get_db_pool] = lambda: runtime_pool
        yield env
    finally:
        env["client"]._transport.app.dependency_overrides[get_db_pool] = lambda: owner_pool
        if runtime_pool is not None:
            await runtime_pool.close()
        async with owner_pool.acquire() as conn:
            await conn.execute(f'DROP OWNED BY "{role}"')
            await conn.execute(f'DROP ROLE "{role}"')


@pytest.mark.parametrize("quantity,movements", [("8", 1), ("12.25", 1), ("0", 1), ("10", 0)])
async def test_nonowner_physical_apply(runtime_env, quantity, movements):
    await physical.test_successful_shortage_surplus_zero_and_no_delta(
        runtime_env, quantity, movements
    )


async def test_nonowner_found_surplus(runtime_env):
    await physical.test_found_item_surplus_creates_missing_row(runtime_env)


async def test_nonowner_release_and_replay(runtime_env):
    await freezes.test_release_is_explicit_audited_and_does_not_release_claims(runtime_env)


async def test_nonowner_standard_fbs(runtime_env, monkeypatch):
    await freezes.test_real_fbs_continues_and_atomically_invalidates(runtime_env, monkeypatch)


async def test_nonowner_multi_source_fbs_retry(runtime_env, monkeypatch):
    await freezes.test_real_multi_source_kiz_fbs_retry_and_duplicate_keep_atomic_invalidation(
        runtime_env, monkeypatch
    )


@pytest.mark.parametrize(
    "kind,source,destination",
    [
        ("receive", None, "zone"),
        ("adjust", None, "zone"),
        ("ship", "zone", None),
        ("transfer", "zone", "empty"),
        ("transfer", "empty", "zone"),
    ],
)
async def test_nonowner_frozen_writer_gets_domain_error(runtime_env, kind, source, destination):
    await freezes.test_ordinary_movement_cannot_cross_freeze(runtime_env, kind, source, destination)


async def test_nonowner_legacy_operations_with_flags_off(runtime_env, monkeypatch):
    env = runtime_env
    for flag in ("INVENTORY_MODULE_ENABLED", "INVENTORY_FREEZE_ENABLED", "INVENTORY_APPLY_ENABLED"):
        monkeypatch.setattr(settings, flag, False)
    async with env["pool"].acquire() as conn:
        loc = await conn.fetchval(
            "INSERT INTO wms.locations(name,level) VALUES('NONOWNER',0) RETURNING location_id"
        )
        for kind, source, destination, qty in [
            ("receive", None, loc, 5),
            ("adjust", None, loc, 2),
            ("adjust", loc, None, 1),
            ("transfer", loc, env["empty"], 2),
            ("ship", loc, None, 1),
        ]:
            await conn.execute(
                "INSERT INTO wms.movements(movement_type,product_id,from_location_id,to_location_id,quantity) VALUES($1,'inv-a',$2,$3,$4)",
                kind,
                source,
                destination,
                qty,
            )
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                loc,
            )
            == 3
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM wms.inventory_location_fences WHERE location_id=$1", loc
            )
            == 1
        )


async def test_privilege_preflight_blocks_guard_installation_and_rolls_back_acl(migration_env):
    env = migration_env
    role = "inventory_writer_" + uuid4().hex
    async with env["pool"].acquire() as conn:
        await conn.execute(f'CREATE ROLE "{role}" NOLOGIN')
        try:
            await grant_legacy(conn, role)
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute(
                "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-a',$1,1)",
                env["zone"],
            )
            await conn.execute("RESET ROLE")
            roster = json.dumps([{"role": role, "profile": "writer"}])
            await conn.execute("SELECT set_config('wms.inventory_runtime_roles',$1,false)", roster)
            await failed_migration(conn, FREEZE, "P7605")
            assert (
                await conn.fetchval("SELECT to_regclass('wms.inventory_location_fences')") is None
            )
            assert not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='aaa_inventory_location_freeze')"
            )
            assert not await conn.fetchval(
                "SELECT has_table_privilege($1,'wms.inventory_documents','SELECT')", role
            )
            # Even an explicit grant to the first role rolls back if a later roster entry fails.
            await conn.execute(
                "SELECT set_config('wms.inventory_grant_runtime_privileges','on',false)"
            )
            await conn.execute(
                "SELECT set_config('wms.inventory_runtime_roles',$1,false)",
                json.dumps(
                    [
                        {"role": role, "profile": "writer"},
                        {"role": "nonexistent_review_runtime", "profile": "writer"},
                    ]
                ),
            )
            await failed_migration(conn, FREEZE, "P7605")
            assert not await conn.fetchval(
                "SELECT has_table_privilege($1,'wms.inventory_documents','SELECT')", role
            )
            await conn.execute("SELECT set_config('wms.inventory_runtime_roles',$1,false)", roster)
            await conn.execute(FREEZE)
            # New application dependencies must be checked before replacing the stub/projection.
            await conn.execute(
                "SELECT set_config('wms.inventory_grant_runtime_privileges','off',false)"
            )
            await failed_migration(conn, APPLY, "P7605")
            assert await conn.fetchval("SELECT to_regclass('wms.inventory_applications')") is None
            await conn.execute(
                "SELECT set_config('wms.inventory_grant_runtime_privileges','on',false)"
            )
            await conn.execute(APPLY)
            await conn.execute(PRIVILEGES_PREFLIGHT)
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute(
                "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-a',$1,1)",
                env["zone"],
            )
            loc = await conn.fetchval(
                "INSERT INTO wms.locations(name,level) VALUES('WRITER-NEW',0) RETURNING location_id"
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM wms.inventory_location_fences WHERE location_id=$1", loc
                )
                == 1
            )
            assert not await conn.fetchval(
                "SELECT has_table_privilege(current_user,'wms.inventory_documents','INSERT')"
            )
            await conn.execute("RESET ROLE")
            await conn.execute(f'REVOKE UPDATE ON wms.inventory_location_fences FROM "{role}"')
            await failed_migration(conn, PRIVILEGES_PREFLIGHT, "P7605")
            await conn.execute(f'GRANT UPDATE ON wms.inventory_location_fences TO "{role}"')
            seq = await conn.fetchval(
                "SELECT pg_get_serial_sequence('wms.inventory_freeze_events','event_id')"
            )
            await conn.execute(f'REVOKE USAGE ON SEQUENCE {seq} FROM "{role}"')
            await failed_migration(conn, PRIVILEGES_PREFLIGHT, "P7605")
            await conn.execute(f'GRANT USAGE ON SEQUENCE {seq} TO "{role}"')
            signature = await conn.fetchval(
                "SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace='wms'::regnamespace "
                "AND proname='inventory_check_location_write'"
            )
            await conn.execute(f'REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC,"{role}"')
            await failed_migration(conn, PRIVILEGES_PREFLIGHT, "P7605")
        finally:
            await conn.execute("RESET ROLE")
            await conn.execute(f'DROP OWNED BY "{role}"')
            await conn.execute(f'DROP ROLE "{role}"')


@pytest.mark.parametrize("runtime_env", ["writer"], indirect=True)
async def test_writer_profile_can_continue_fbs_under_operator_freeze(runtime_env, monkeypatch):
    env = runtime_env
    app = env["client"]._transport.app
    app.dependency_overrides[get_db_pool] = lambda: env["owner_pool"]
    doc, _ = await freezes.begin(env)
    await freezes.confirm(env, doc)
    app.dependency_overrides[get_db_pool] = lambda: env["pool"]
    response = await freezes.fbs_http(env, monkeypatch)
    assert response.status_code == 201, response.text
    async with env["pool"].acquire() as conn:
        assert await conn.fetchval("SELECT state FROM wms.inventory_freezes") == "invalidated"
        assert (
            await conn.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 9
        )
        assert not await conn.fetchval(
            "SELECT has_table_privilege(current_user,'wms.inventory_documents','INSERT')"
        )


async def test_nonowner_metadata_locking_uses_column_scoped_grants(runtime_env):
    env = runtime_env
    async with env["owner_pool"].acquire() as admin:
        async with env["pool"].acquire() as conn:
            role = await conn.fetchval("SELECT current_user")
        await admin.execute(
            "SELECT set_config('wms.inventory_runtime_roles',$1,false), "
            "set_config('wms.inventory_grant_runtime_privileges','on',false)",
            json.dumps([{"role": role, "profile": "inventory"}]),
        )
        await admin.execute(f'REVOKE UPDATE ON public.products,wms.locations FROM "{role}"')
        await failed_migration(admin, PRIVILEGES_PREFLIGHT, "P7605")
        await admin.execute("SELECT wms.inventory_runtime_privileges('apply')")
    async with env["pool"].acquire() as conn:
        assert not await conn.fetchval(
            "SELECT has_table_privilege(current_user,'public.products','UPDATE')"
        )
        assert await conn.fetchval(
            "SELECT has_column_privilege(current_user,'public.products','id','UPDATE')"
        )
        assert not await conn.fetchval(
            "SELECT has_column_privilege(current_user,'public.products','name','UPDATE')"
        )
    await physical.test_successful_shortage_surplus_zero_and_no_delta(env, "8", 1)
