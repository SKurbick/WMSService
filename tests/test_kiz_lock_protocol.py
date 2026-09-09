"""Design counterexample only; requires an explicitly selected local test database."""
import asyncio
import os
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest


@pytest.mark.parametrize(
    "isolation,touch,expected_state,expected_physical",
    [
        ("read_committed", False, "P7501", 5),
        ("repeatable_read", False, None, 4),
        ("repeatable_read", True, "40001", 5),
    ],
)
def test_kiz_snapshot_protocol(isolation, touch, expected_state, expected_physical):
    dsn = os.getenv("KIZ_LOCK_TEST_DSN")
    if not dsn:
        pytest.skip("Set KIZ_LOCK_TEST_DSN to a disposable local kiz_test database")
    target = urlparse(dsn)
    if target.hostname not in {"localhost", "127.0.0.1", "::1"} or target.path != "/kiz_test":
        pytest.fail("Only a local disposable database named kiz_test is allowed")

    async def run():
        schema = "kiz_probe_" + uuid4().hex
        assignment = await asyncpg.connect(dsn)
        writer = None
        created = False
        try:
            writer = await asyncpg.connect(dsn)
            await assignment.execute(f"CREATE SCHEMA {schema}")
            created = True
            await assignment.execute(f"SET search_path TO {schema}")
            await writer.execute(f"SET search_path TO {schema}")
            await assignment.execute("""
                CREATE TABLE inventory (id integer PRIMARY KEY, quantity numeric NOT NULL);
                CREATE TABLE kiz (
                    code text PRIMARY KEY, scope integer NOT NULL,
                    lifecycle_status text NOT NULL DEFAULT 'active'
                );
                INSERT INTO inventory VALUES (1, 5);
                INSERT INTO kiz (code, scope)
                    SELECT 'initial-' || n, 1 FROM generate_series(1, 4) n;
                CREATE FUNCTION guard() RETURNS trigger
                LANGUAGE plpgsql VOLATILE AS $$
                DECLARE identified bigint;
                BEGIN
                    IF NEW.quantity >= OLD.quantity THEN RETURN NEW; END IF;
                    SELECT count(*) INTO identified FROM kiz
                    WHERE scope = OLD.id AND lifecycle_status = 'active';
                    IF NEW.quantity < identified THEN
                        RAISE EXCEPTION USING ERRCODE = 'P7501',
                            MESSAGE = 'insufficient unidentified stock';
                    END IF;
                    RETURN NEW;
                END;
                $$;
                CREATE TRIGGER inventory_guard BEFORE UPDATE ON inventory
                    FOR EACH ROW EXECUTE FUNCTION guard();
            """)
            sqlstate = None
            try:
                async with writer.transaction(isolation=isolation):
                    # Establish the writer snapshot before assignment commits.
                    assert await writer.fetchval("SELECT count(*) FROM kiz") == 4
                    async with assignment.transaction(isolation="read_committed"):
                        physical = await assignment.fetchval(
                            "SELECT quantity FROM inventory WHERE id=1 FOR UPDATE"
                        )
                        identified = await assignment.fetchval("SELECT count(*) FROM kiz")
                        assert physical - identified == 1
                        if touch:
                            # Proposed amendment: a new row version, same physical quantity.
                            await assignment.execute(
                                "UPDATE inventory SET quantity=quantity WHERE id=1"
                            )
                        await assignment.execute(
                            "INSERT INTO kiz (code,scope) VALUES ('last',1)"
                        )
                    await writer.execute("UPDATE inventory SET quantity=4 WHERE id=1")
            except asyncpg.PostgresError as exc:
                sqlstate = exc.sqlstate

            assert sqlstate == expected_state
            assert await assignment.fetchval("SELECT quantity FROM inventory") == expected_physical
            assert await assignment.fetchval("SELECT count(*) FROM kiz") == 5
        finally:
            if writer is not None:
                await writer.close()
            try:
                if created:
                    await assignment.execute(f"DROP SCHEMA {schema} CASCADE")
            finally:
                await assignment.close()

    asyncio.run(run())
