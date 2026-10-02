BEGIN;

ALTER TABLE wms.fbs_shipment_items
    ADD COLUMN IF NOT EXISTS task_resolution_status varchar(40);

ALTER TABLE wms.fbs_shipment_items
    DROP CONSTRAINT IF EXISTS chk_fbs_item_task_resolution_status;

ALTER TABLE wms.fbs_shipment_items
    ADD CONSTRAINT chk_fbs_item_task_resolution_status CHECK (
        task_resolution_status IS NULL OR task_resolution_status IN (
            'completed',
            'completed_with_duplicates',
            'duplicate_only',
            'partially_completed',
            'pending_retry',
            'failed'
        )
    );

CREATE TABLE IF NOT EXISTS wms.fbs_shipment_task_results (
    result_id                       bigserial PRIMARY KEY,
    shipment_id                    integer NOT NULL,
    item_id                        integer NOT NULL,
    occurrence_index               integer NOT NULL,
    task_id                        bigint NOT NULL,
    product_id                     varchar NOT NULL,
    outcome                        varchar(40) NOT NULL,
    effect_quantity                smallint NOT NULL DEFAULT 0,
    movement_id                    bigint,
    movement_created_at            timestamptz,
    existing_success_item_id       integer,
    existing_movement_id           bigint,
    existing_movement_created_at   timestamptz,
    is_shipped_before              boolean,
    reason                         text,
    details                        jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempt_count                  integer NOT NULL DEFAULT 1,
    first_processed_at             timestamptz NOT NULL DEFAULT now(),
    last_processed_at              timestamptz NOT NULL DEFAULT now(),
    last_error                     text,
    created_at                     timestamptz NOT NULL DEFAULT now(),
    updated_at                     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT fk_fbs_task_result_shipment
        FOREIGN KEY (shipment_id)
        REFERENCES wms.fbs_shipments(shipment_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_fbs_task_result_item
        FOREIGN KEY (item_id)
        REFERENCES wms.fbs_shipment_items(item_id)
        ON DELETE CASCADE,
    CONSTRAINT chk_fbs_task_result_occurrence
        CHECK (occurrence_index >= 0),
    CONSTRAINT chk_fbs_task_result_outcome CHECK (
        outcome IN (
            'written_off',
            'duplicate_skipped',
            'duplicate_in_payload',
            'inconsistent',
            'not_found',
            'pending_retry',
            'failed'
        )
    ),
    CONSTRAINT chk_fbs_task_result_effect CHECK (effect_quantity IN (0, 1)),
    CONSTRAINT chk_fbs_task_result_movement_pair CHECK (
        (movement_id IS NULL) = (movement_created_at IS NULL)
    ),
    CONSTRAINT chk_fbs_task_result_existing_movement_pair CHECK (
        (existing_movement_id IS NULL) = (existing_movement_created_at IS NULL)
    ),
    CONSTRAINT chk_fbs_task_result_written_off CHECK (
        outcome <> 'written_off'
        OR (
            effect_quantity = 1
            AND movement_id IS NOT NULL
            AND movement_created_at IS NOT NULL
        )
    ),
    CONSTRAINT uq_fbs_task_result_item_occurrence
        UNIQUE (item_id, occurrence_index)
);

CREATE INDEX IF NOT EXISTS idx_fbs_task_results_task
    ON wms.fbs_shipment_task_results(task_id);

CREATE INDEX IF NOT EXISTS idx_fbs_task_results_shipment_outcome
    ON wms.fbs_shipment_task_results(shipment_id, outcome);

CREATE INDEX IF NOT EXISTS idx_fbs_task_results_product_updated
    ON wms.fbs_shipment_task_results(product_id, updated_at DESC);

COMMIT;
