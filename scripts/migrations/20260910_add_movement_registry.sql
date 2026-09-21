-- KIZ Stage 2A, Phase 1: stable identity for partitioned wms.movements.
-- Apply manually before running the historical backfill. PostgreSQL >= 15.
-- This migration does not create movements and does not change inventory.

BEGIN;
SET LOCAL lock_timeout = '10s';

-- The preflight and the constraint must see the same write-free state. PostgreSQL
-- cannot build a unique index on a partitioned parent CONCURRENTLY, so this lock is
-- intentionally fail-fast. Retry the whole migration in a controlled deployment window.
LOCK TABLE wms.movements IN SHARE ROW EXCLUSIVE MODE;

DO $$
DECLARE
    duplicate_row record;
BEGIN
    SELECT movement_id, created_at, count(*) AS row_count
    INTO duplicate_row
    FROM wms.movements
    GROUP BY movement_id, created_at
    HAVING count(*) > 1
    ORDER BY created_at, movement_id
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23505',
            MESSAGE = 'Cannot create movement identity: duplicate (movement_id, created_at)',
            DETAIL = format(
                'movement_id=%s, created_at=%s, row_count=%s',
                duplicate_row.movement_id,
                duplicate_row.created_at,
                duplicate_row.row_count
            );
    END IF;
END;
$$;

ALTER TABLE wms.movements
    ADD CONSTRAINT uq_movements_movement_id_created_at
    UNIQUE (movement_id, created_at);

CREATE TABLE wms.movement_registry (
    movement_ref bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    movement_id bigint NOT NULL,
    movement_created_at timestamptz NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_movement_registry_coordinate
        UNIQUE (movement_id, movement_created_at),
    CONSTRAINT fk_movement_registry_movement
        FOREIGN KEY (movement_id, movement_created_at)
        REFERENCES wms.movements (movement_id, created_at)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
);

CREATE FUNCTION wms.register_movement_identity() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, wms
AS $$
BEGIN
    INSERT INTO wms.movement_registry (movement_id, movement_created_at)
    VALUES (NEW.movement_id, NEW.created_at);
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_register_movement_identity
AFTER INSERT ON wms.movements
FOR EACH ROW EXECUTE FUNCTION wms.register_movement_identity();

CREATE FUNCTION wms.guard_movement_registry_immutable() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, wms
AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '55000',
        MESSAGE = 'movement_registry mappings are immutable';
END;
$$;

CREATE TRIGGER trg_movement_registry_immutable
BEFORE UPDATE OR DELETE ON wms.movement_registry
FOR EACH ROW EXECUTE FUNCTION wms.guard_movement_registry_immutable();

-- Call this function in separate transactions until it returns 0. Row locks with
-- SKIP LOCKED allow more than one administrative worker, while ON CONFLICT makes a
-- retry safe. New movements are registered by the trigger and need no backfill.
CREATE FUNCTION wms.backfill_movement_registry(p_batch_size integer DEFAULT 10000)
RETURNS integer
LANGUAGE plpgsql
VOLATILE
SET search_path = pg_catalog, wms
AS $$
DECLARE
    inserted_rows integer;
BEGIN
    IF p_batch_size IS NULL OR p_batch_size < 1 OR p_batch_size > 100000 THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'movement registry batch size must be between 1 and 100000';
    END IF;

    WITH missing AS MATERIALIZED (
        SELECT m.movement_id, m.created_at
        FROM wms.movements AS m
        WHERE NOT EXISTS (
            SELECT 1
            FROM wms.movement_registry AS r
            WHERE r.movement_id = m.movement_id
              AND r.movement_created_at = m.created_at
        )
        ORDER BY m.created_at, m.movement_id
        LIMIT p_batch_size
        FOR UPDATE OF m SKIP LOCKED
    ), inserted AS (
        INSERT INTO wms.movement_registry (movement_id, movement_created_at)
        SELECT movement_id, created_at
        FROM missing
        ON CONFLICT (movement_id, movement_created_at) DO NOTHING
        RETURNING 1
    )
    SELECT count(*)::integer INTO inserted_rows FROM inserted;

    RETURN inserted_rows;
END;
$$;

-- One statement gives a transactionally consistent coverage result. Constraint-backed
-- problems should stay zero; they are exposed so the deployment check is explicit.
CREATE FUNCTION wms.check_movement_registry_integrity()
RETURNS TABLE (
    movement_rows bigint,
    registry_rows bigint,
    missing_registry_rows bigint,
    orphan_registry_rows bigint,
    duplicate_movement_coordinates bigint,
    duplicate_registry_coordinates bigint,
    is_complete boolean
)
LANGUAGE sql
STABLE
SET search_path = pg_catalog, wms
AS $$
    WITH
    movement_count AS (
        SELECT count(*) AS value FROM wms.movements
    ),
    registry_count AS (
        SELECT count(*) AS value FROM wms.movement_registry
    ),
    missing_count AS (
        SELECT count(*) AS value
        FROM wms.movements AS m
        WHERE NOT EXISTS (
            SELECT 1 FROM wms.movement_registry AS r
            WHERE r.movement_id = m.movement_id
              AND r.movement_created_at = m.created_at
        )
    ),
    orphan_count AS (
        SELECT count(*) AS value
        FROM wms.movement_registry AS r
        WHERE NOT EXISTS (
            SELECT 1 FROM wms.movements AS m
            WHERE m.movement_id = r.movement_id
              AND m.created_at = r.movement_created_at
        )
    ),
    duplicate_movements AS (
        SELECT count(*) AS value
        FROM (
            SELECT 1 FROM wms.movements
            GROUP BY movement_id, created_at HAVING count(*) > 1
        ) AS duplicates
    ),
    duplicate_registry AS (
        SELECT count(*) AS value
        FROM (
            SELECT 1 FROM wms.movement_registry
            GROUP BY movement_id, movement_created_at HAVING count(*) > 1
        ) AS duplicates
    )
    SELECT
        movement_count.value,
        registry_count.value,
        missing_count.value,
        orphan_count.value,
        duplicate_movements.value,
        duplicate_registry.value,
        missing_count.value = 0
            AND orphan_count.value = 0
            AND duplicate_movements.value = 0
            AND duplicate_registry.value = 0
            AND movement_count.value = registry_count.value
    FROM movement_count, registry_count, missing_count, orphan_count,
         duplicate_movements, duplicate_registry;
$$;

COMMENT ON TABLE wms.movement_registry IS
'Stable identity mapping for the partitioned movement ledger. One immutable row per exact (movement_id, created_at) coordinate.';
COMMENT ON COLUMN wms.movement_registry.movement_ref IS
'Internal stable reference for future domain associations; public legacy movement IDs remain unchanged.';
COMMENT ON FUNCTION wms.backfill_movement_registry(integer) IS
'Resumable historical movement registry backfill. Invoke in separate transactions until it returns zero.';
COMMENT ON FUNCTION wms.check_movement_registry_integrity() IS
'Read-only coverage and constraint-oriented integrity summary for movement_registry.';

COMMIT;
