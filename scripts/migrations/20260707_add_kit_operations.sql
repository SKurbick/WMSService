-- Add kit assembly/disassembly operation journal, source linkage and allowed operation locations.

BEGIN;

ALTER TABLE wms.movements
    ADD COLUMN IF NOT EXISTS source_type varchar(50),
    ADD COLUMN IF NOT EXISTS source_id bigint,
    ADD COLUMN IF NOT EXISTS source_item_id bigint;

DO $$
DECLARE
    rel regclass;
BEGIN
    FOR rel IN
        SELECT 'wms.movements'::regclass
        UNION ALL
        SELECT inhrelid::regclass
        FROM pg_inherits
        WHERE inhparent = 'wms.movements'::regclass
    LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT IF EXISTS chk_movement_type', rel);
    END LOOP;
END $$;

ALTER TABLE wms.movements
    ADD CONSTRAINT chk_movement_type CHECK (
        movement_type IN (
            'receive',
            'putaway',
            'transfer',
            'pick',
            'ship',
            'unpack',
            'adjust',
            'kit_assembly',
            'kit_disassembly'
        )
    );

CREATE TABLE IF NOT EXISTS wms.operation_locations (
    operation_location_id bigserial PRIMARY KEY,
    operation_code varchar(64) NOT NULL,
    location_id bigint NOT NULL,
    location_code varchar(100) NOT NULL,
    scope varchar(32) NOT NULL DEFAULT 'direct',
    is_active boolean NOT NULL DEFAULT true,
    author varchar(100),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_operation_locations_scope CHECK (scope IN ('direct')),
    CONSTRAINT fk_operation_locations_location FOREIGN KEY (location_id)
        REFERENCES wms.locations(location_id) ON DELETE RESTRICT,
    CONSTRAINT uq_operation_locations_operation_location_scope UNIQUE (
        operation_code,
        location_id,
        scope
    )
);

ALTER TABLE wms.operation_locations
    ADD COLUMN IF NOT EXISTS operation_code varchar(64),
    ADD COLUMN IF NOT EXISTS location_id bigint,
    ADD COLUMN IF NOT EXISTS location_code varchar(100),
    ADD COLUMN IF NOT EXISTS scope varchar(32) DEFAULT 'direct',
    ADD COLUMN IF NOT EXISTS is_active boolean DEFAULT true,
    ADD COLUMN IF NOT EXISTS author varchar(100),
    ADD COLUMN IF NOT EXISTS metadata jsonb DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_operation_locations_operation_location_scope'
          AND conrelid = 'wms.operation_locations'::regclass
    ) THEN
        ALTER TABLE wms.operation_locations
            ADD CONSTRAINT uq_operation_locations_operation_location_scope
            UNIQUE (operation_code, location_id, scope);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS wms.kit_operations (
    operation_id bigserial PRIMARY KEY,
    operation_location_id bigint,
    operation_type varchar(50) NOT NULL,
    kit_product_id varchar(50) NOT NULL,
    quantity numeric(10,2) NOT NULL,
    location_id bigint NOT NULL,
    location_code varchar(100),
    author varchar(100) NOT NULL,
    status varchar(50) NOT NULL DEFAULT 'processing',
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT chk_kit_operations_type CHECK (operation_type IN ('assembly', 'disassembly')),
    CONSTRAINT chk_kit_operations_status CHECK (status IN ('processing', 'completed', 'failed')),
    CONSTRAINT chk_kit_operations_quantity CHECK (quantity > 0),
    CONSTRAINT fk_kit_operations_product FOREIGN KEY (kit_product_id)
        REFERENCES public.products(id) ON DELETE RESTRICT,
    CONSTRAINT fk_kit_operations_location FOREIGN KEY (location_id)
        REFERENCES wms.locations(location_id) ON DELETE RESTRICT
);

ALTER TABLE wms.kit_operations
    ADD COLUMN IF NOT EXISTS operation_location_id bigint,
    ADD COLUMN IF NOT EXISTS location_code varchar(100);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_kit_operations_operation_location'
          AND conrelid = 'wms.kit_operations'::regclass
    ) THEN
        ALTER TABLE wms.kit_operations
            ADD CONSTRAINT fk_kit_operations_operation_location
            FOREIGN KEY (operation_location_id)
            REFERENCES wms.operation_locations(operation_location_id)
            ON DELETE RESTRICT;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS wms.kit_operation_items (
    item_id bigserial PRIMARY KEY,
    operation_id bigint NOT NULL REFERENCES wms.kit_operations(operation_id) ON DELETE CASCADE,
    role varchar(50) NOT NULL,
    product_id varchar(50) NOT NULL,
    quantity_per_kit numeric(10,2) NOT NULL,
    total_quantity numeric(10,2) NOT NULL,
    movement_id bigint,
    movement_created_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_kit_operation_items_role CHECK (
        role IN (
            'component_consumption',
            'kit_result',
            'kit_consumption',
            'component_result'
        )
    ),
    CONSTRAINT chk_kit_operation_items_quantity_per_kit CHECK (quantity_per_kit > 0),
    CONSTRAINT chk_kit_operation_items_total_quantity CHECK (total_quantity > 0),
    CONSTRAINT fk_kit_operation_items_product FOREIGN KEY (product_id)
        REFERENCES public.products(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_operation_locations_code_active
    ON wms.operation_locations(operation_code, location_code, is_active);
CREATE INDEX IF NOT EXISTS idx_operation_locations_location_active
    ON wms.operation_locations(operation_code, location_id, is_active);
CREATE INDEX IF NOT EXISTS idx_kit_operations_created_at
    ON wms.kit_operations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kit_operations_filters
    ON wms.kit_operations(operation_type, kit_product_id, status, location_id);
CREATE INDEX IF NOT EXISTS idx_kit_operations_operation_location
    ON wms.kit_operations(operation_location_id);
CREATE INDEX IF NOT EXISTS idx_kit_operation_items_operation
    ON wms.kit_operation_items(operation_id);
CREATE INDEX IF NOT EXISTS idx_movements_source
    ON wms.movements(source_type, source_id, source_item_id);

COMMENT ON TABLE wms.operation_locations IS 'Разрешённые WMS-локации для доменных операций, включая kit_operations.';
COMMENT ON TABLE wms.kit_operations IS 'Журнал операций комплектации и разукомплектации комплектов.';
COMMENT ON TABLE wms.kit_operation_items IS 'Строки операций комплектов и связь с созданными movements.';
COMMENT ON COLUMN wms.operation_locations.scope IS 'direct означает использование только inventory.location_id этой строки, без subtree.';
COMMENT ON COLUMN wms.kit_operations.operation_location_id IS 'Разрешённая локация операции из wms.operation_locations.';
COMMENT ON COLUMN wms.kit_operations.location_code IS 'Снапшот кода локации на момент операции.';
COMMENT ON COLUMN wms.movements.source_type IS 'Источник движения, например kit_operation.';
COMMENT ON COLUMN wms.movements.source_id IS 'ID сущности-источника движения.';
COMMENT ON COLUMN wms.movements.source_item_id IS 'ID строки сущности-источника движения.';

COMMIT;
