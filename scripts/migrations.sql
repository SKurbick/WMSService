-- =========================================================
-- 1. Таблица текущего состояния резервов
-- =========================================================

CREATE TABLE IF NOT EXISTS wms.stock_reservation_orders (
    reservation_order_id bigserial PRIMARY KEY,

    source_type text NOT NULL DEFAULT 'fbs',
    product_id text NOT NULL,

    external_order_id bigint NOT NULL,
    external_status text NOT NULL,

    is_reserved boolean NOT NULL,
    reserved_qty numeric(20,3) NOT NULL DEFAULT 1,

    external_created_at timestamptz NULL,
    last_event_at timestamptz NOT NULL DEFAULT now(),

    raw_payload jsonb NULL,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_stock_reservation_order
        UNIQUE (source_type, product_id, external_order_id),

    CONSTRAINT fk_stock_reservation_product
        FOREIGN KEY (product_id)
        REFERENCES public.products(id)
        ON DELETE RESTRICT
);

COMMENT ON TABLE wms.stock_reservation_orders IS
'Текущее состояние мягких резервов товаров по внешним заказам. Резерв не меняет wms.inventory и не создает wms.movements.';

COMMENT ON COLUMN wms.stock_reservation_orders.source_type IS
'Источник резерва, например fbs.';

COMMENT ON COLUMN wms.stock_reservation_orders.product_id IS
'Product ID из public.products.id / wms.inventory.product_id. Приходит из RabbitMQ поля wild.';

COMMENT ON COLUMN wms.stock_reservation_orders.external_order_id IS
'Внешний order_id. По бизнес-правилу 1 order_id = 1 штука товара.';

COMMENT ON COLUMN wms.stock_reservation_orders.is_reserved IS
'true = заказ держит активный резерв, false = резерв снят.';

COMMENT ON COLUMN wms.stock_reservation_orders.reserved_qty IS
'Количество в резерве. В MVP всегда 1, но поле оставлено для будущего расширения.';


-- =========================================================
-- 2. Audit-таблица входящих событий резерва
-- =========================================================

CREATE TABLE IF NOT EXISTS wms.stock_reservation_events (
    reservation_event_id bigserial PRIMARY KEY,

    source_type text NOT NULL DEFAULT 'fbs',
    product_id text NULL,
    external_order_id bigint NULL,
    external_status text NULL,

    reserved_qty numeric(20,3) NULL,

    external_created_at timestamptz NULL,
    event_received_at timestamptz NOT NULL DEFAULT now(),

    processing_result text NOT NULL,
    error_message text NULL,

    raw_payload jsonb NOT NULL
);

COMMENT ON TABLE wms.stock_reservation_events IS
'Audit-log входящих RabbitMQ-событий по резервам: успешные, повторные, неизвестные статусы, неизвестные товары, ошибки валидации.';

COMMENT ON COLUMN wms.stock_reservation_events.processing_result IS
'Результат обработки события: processed, released, unknown_status, product_not_found, invalid_payload, db_error и т.д.';


-- =========================================================
-- 3. Индексы для stock_reservation_orders
-- =========================================================

CREATE INDEX IF NOT EXISTS idx_stock_reservation_orders_product
    ON wms.stock_reservation_orders (product_id);

CREATE INDEX IF NOT EXISTS idx_stock_reservation_orders_is_reserved
    ON wms.stock_reservation_orders (is_reserved);

CREATE INDEX IF NOT EXISTS idx_stock_reservation_orders_product_reserved
    ON wms.stock_reservation_orders (product_id, is_reserved);

CREATE INDEX IF NOT EXISTS idx_stock_reservation_orders_external_order
    ON wms.stock_reservation_orders (external_order_id);

CREATE INDEX IF NOT EXISTS idx_stock_reservation_orders_status
    ON wms.stock_reservation_orders (external_status);

CREATE INDEX IF NOT EXISTS idx_stock_reservation_orders_last_event_at
    ON wms.stock_reservation_orders (last_event_at);

-- Полезно для аудита старых активных резервов:
CREATE INDEX IF NOT EXISTS idx_stock_reservation_orders_stale_active
    ON wms.stock_reservation_orders (last_event_at)
    WHERE is_reserved = true;


-- =========================================================
-- 4. Индексы для stock_reservation_events
-- =========================================================

CREATE INDEX IF NOT EXISTS idx_stock_reservation_events_product
    ON wms.stock_reservation_events (product_id);

CREATE INDEX IF NOT EXISTS idx_stock_reservation_events_external_order
    ON wms.stock_reservation_events (external_order_id);

CREATE INDEX IF NOT EXISTS idx_stock_reservation_events_status
    ON wms.stock_reservation_events (external_status);

CREATE INDEX IF NOT EXISTS idx_stock_reservation_events_processing_result
    ON wms.stock_reservation_events (processing_result);

CREATE INDEX IF NOT EXISTS idx_stock_reservation_events_received_at
    ON wms.stock_reservation_events (event_received_at DESC);

CREATE INDEX IF NOT EXISTS idx_stock_reservation_events_product_received_at
    ON wms.stock_reservation_events (product_id, event_received_at DESC);


-- =========================================================
-- 5. Trigger для updated_at
-- =========================================================
-- В проекте уже есть функция wms.update_updated_at_column()
-- если она действительно есть в БД, используем её.

DROP TRIGGER IF EXISTS trg_stock_reservation_orders_updated_at
ON wms.stock_reservation_orders;

CREATE TRIGGER trg_stock_reservation_orders_updated_at
BEFORE UPDATE ON wms.stock_reservation_orders
FOR EACH ROW
EXECUTE FUNCTION wms.update_updated_at_column();


-- =========================================================
-- 6. View доступности товара
-- =========================================================

CREATE OR REPLACE VIEW wms.v_product_availability AS
WITH physical AS (
    SELECT
        product_id,
        SUM(quantity) AS physical_qty
    FROM wms.inventory
    WHERE status = 'available'
    GROUP BY product_id
),
reserved AS (
    SELECT
        product_id,
        SUM(reserved_qty) AS reserved_qty
    FROM wms.stock_reservation_orders
    WHERE is_reserved = true
    GROUP BY product_id
)
SELECT
    COALESCE(p.product_id, r.product_id) AS product_id,
    COALESCE(p.physical_qty, 0) AS physical_qty,
    COALESCE(r.reserved_qty, 0) AS reserved_qty,
    COALESCE(p.physical_qty, 0) - COALESCE(r.reserved_qty, 0) AS free_qty,
    GREATEST(
        COALESCE(r.reserved_qty, 0) - COALESCE(p.physical_qty, 0),
        0
    ) AS shortage_qty
FROM physical p
FULL JOIN reserved r ON r.product_id = p.product_id;

COMMENT ON VIEW wms.v_product_availability IS
'Расчет доступности товара: физический available остаток минус активные мягкие резервы. free_qty может быть отрицательным и показывает нехватку.';