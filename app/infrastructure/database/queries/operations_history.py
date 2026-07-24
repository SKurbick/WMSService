"""SQL нормализованного списка бизнес-операций WMS."""

LOCATION_EXISTS = "SELECT EXISTS(SELECT 1 FROM wms.locations WHERE location_id = $1)"

_EVENTS_CTES = """
WITH params AS (
    SELECT
        ($1::date::timestamp AT TIME ZONE 'Europe/Moscow') AS period_start,
        (($2::date + 1)::timestamp AT TIME ZONE 'Europe/Moscow') AS period_end
),
period_movements AS MATERIALIZED (
    SELECT m.*
    FROM wms.movements m
    CROSS JOIN params p
    WHERE m.created_at >= p.period_start AND m.created_at < p.period_end
      AND ($3::text IS NULL OR $3 = 'movement')
      AND ($4::text IS NULL OR m.movement_type = $4)
      AND ($5::varchar IS NULL OR m.product_id = $5)
      AND ($6::bigint IS NULL OR m.from_location_id = $6 OR m.to_location_id = $6)
      AND ($7::text IS NULL OR m.user_name = $7)
      AND $8::text IS NULL
      AND m.source_type IS DISTINCT FROM 'kit_operation'
      AND m.source_type IS DISTINCT FROM 're_sorting_operation'
),
fbs_movement_ids AS (
    SELECT DISTINCT i.movement_id
    FROM wms.fbs_shipment_items i
    JOIN (SELECT DISTINCT movement_id FROM period_movements) p
      ON p.movement_id = i.movement_id
    WHERE i.movement_id IS NOT NULL
),
fbs_movement_matches AS (
    SELECT m.movement_id, count(*)::bigint AS matches
    FROM wms.movements m
    JOIN fbs_movement_ids f ON f.movement_id = m.movement_id
    GROUP BY m.movement_id
),
unambiguous_fbs_movement_ids AS (
    SELECT movement_id
    FROM fbs_movement_matches
    WHERE matches = 1
),
kit_events AS (
    SELECT
        ('kit_operation:' || ko.operation_id)::text AS event_id,
        'kit_operation'::text AS source_type,
        CASE ko.operation_type
            WHEN 'assembly' THEN 'kit_assembly'
            WHEN 'disassembly' THEN 'kit_disassembly'
            ELSE ko.operation_type
        END::text AS operation_type,
        CASE ko.operation_type
            WHEN 'assembly' THEN 'Комплектация'
            WHEN 'disassembly' THEN 'Разукомплектация'
            ELSE ko.operation_type
        END::text AS operation_name,
        ko.status::text AS status,
        ko.created_at::timestamptz AS created_at,
        ko.completed_at::timestamptz AS completed_at,
        ko.author::text AS author,
        ko.location_id::bigint AS location_id,
        COALESCE(ko.location_code, l.location_code)::text AS location_code,
        (SELECT count(DISTINCT i.product_id) FROM wms.kit_operation_items i
         WHERE i.operation_id = ko.operation_id)::bigint AS product_count,
        ko.quantity::numeric AS total_quantity,
        NULL::text AS external_reference
    FROM wms.kit_operations ko
    LEFT JOIN wms.locations l ON l.location_id = ko.location_id
    CROSS JOIN params p
    WHERE ko.created_at >= p.period_start AND ko.created_at < p.period_end
      AND ($3::text IS NULL OR $3 = 'kit_operation')
      AND ($4::text IS NULL OR $4 = CASE ko.operation_type
            WHEN 'assembly' THEN 'kit_assembly'
            WHEN 'disassembly' THEN 'kit_disassembly'
            ELSE ko.operation_type END)
      AND ($5::varchar IS NULL OR EXISTS (
            SELECT 1 FROM wms.kit_operation_items i
            WHERE i.operation_id = ko.operation_id AND i.product_id = $5))
      AND ($6::bigint IS NULL OR ko.location_id = $6)
      AND ($7::text IS NULL OR ko.author = $7)
      AND ($8::text IS NULL OR ko.status = $8)
),
re_sorting_events AS (
    SELECT
        ('re_sorting:' || ro.operation_id)::text AS event_id,
        're_sorting_operation'::text AS source_type,
        're_sorting'::text AS operation_type,
        'Пересортица'::text AS operation_name,
        ro.status::text AS status,
        ro.created_at::timestamptz AS created_at,
        ro.completed_at::timestamptz AS completed_at,
        ro.author::text AS author,
        ro.location_id::bigint AS location_id,
        COALESCE(ro.location_code, l.location_code)::text AS location_code,
        (SELECT count(DISTINCT i.product_id) FROM wms.re_sorting_operation_items i
         WHERE i.operation_id = ro.operation_id)::bigint AS product_count,
        ro.quantity::numeric AS total_quantity,
        NULL::text AS external_reference
    FROM wms.re_sorting_operations ro
    LEFT JOIN wms.locations l ON l.location_id = ro.location_id
    CROSS JOIN params p
    WHERE ro.created_at >= p.period_start AND ro.created_at < p.period_end
      AND ($3::text IS NULL OR $3 = 're_sorting_operation')
      AND ($4::text IS NULL OR $4 = 're_sorting')
      AND ($5::varchar IS NULL OR ro.from_product_id = $5 OR ro.to_product_id = $5)
      AND ($6::bigint IS NULL OR ro.location_id = $6)
      AND ($7::text IS NULL OR ro.author = $7)
      AND ($8::text IS NULL OR ro.status = $8)
),
fbs_events AS (
    SELECT
        ('fbs_shipment:' || fs.shipment_id)::text AS event_id,
        'fbs_shipment'::text AS source_type,
        'fbs_shipment'::text AS operation_type,
        'ФБС-отгрузка'::text AS operation_name,
        fs.status::text AS status,
        fs.received_at::timestamptz AS created_at,
        fs.completed_at::timestamptz AS completed_at,
        CASE
            WHEN count(i.item_id) > 0
             AND count(*) FILTER (WHERE NULLIF(btrim(i.author), '') IS NULL) = 0
             AND count(DISTINCT i.author) = 1
            THEN max(i.author)
            ELSE NULL
        END::text AS author,
        NULL::bigint AS location_id,
        NULL::text AS location_code,
        count(DISTINCT i.product_id)::bigint AS product_count,
        COALESCE(sum(i.quantity), 0)::numeric AS total_quantity,
        CASE
            WHEN count(i.item_id) > 0
             AND count(*) FILTER (WHERE NULLIF(btrim(i.supply_id), '') IS NULL) = 0
             AND count(DISTINCT i.supply_id) = 1
            THEN max(i.supply_id)
            ELSE NULL
        END::text AS external_reference
    FROM wms.fbs_shipments fs
    LEFT JOIN wms.fbs_shipment_items i ON i.shipment_id = fs.shipment_id
    CROSS JOIN params p
    WHERE fs.received_at >= p.period_start AND fs.received_at < p.period_end
      AND ($3::text IS NULL OR $3 = 'fbs_shipment')
      AND ($4::text IS NULL OR $4 = 'fbs_shipment')
      AND $6::bigint IS NULL
      AND ($8::text IS NULL OR fs.status = $8)
    GROUP BY fs.shipment_id, fs.status, fs.received_at, fs.completed_at
    HAVING ($5::varchar IS NULL OR bool_or(i.product_id = $5))
       AND ($7::text IS NULL OR bool_or(i.author = $7))
),
standalone_movement_events AS (
    SELECT
        ('movement:' || m.movement_id || ':' ||
         (extract(epoch FROM m.created_at) * 1000000)::bigint)::text AS event_id,
        'movement'::text AS source_type,
        m.movement_type::text AS operation_type,
        CASE m.movement_type
            WHEN 'receive' THEN 'Поступление'
            WHEN 'putaway' THEN 'Размещение'
            WHEN 'transfer' THEN 'Перемещение'
            WHEN 'pick' THEN 'Отбор'
            WHEN 'ship' THEN 'Отгрузка'
            WHEN 'unpack' THEN 'Распаковка'
            WHEN 'adjust' THEN 'Корректировка'
            WHEN 'kit_assembly' THEN 'Комплектация'
            WHEN 'kit_disassembly' THEN 'Разукомплектация'
            WHEN 're_sorting' THEN 'Пересортица'
            ELSE m.movement_type
        END::text AS operation_name,
        NULL::text AS status,
        m.created_at::timestamptz AS created_at,
        NULL::timestamptz AS completed_at,
        m.user_name::text AS author,
        CASE
            WHEN m.from_location_id IS NULL THEN m.to_location_id
            WHEN m.to_location_id IS NULL THEN m.from_location_id
            ELSE NULL
        END::bigint AS location_id,
        CASE
            WHEN m.from_location_id IS NULL THEN l_to.location_code
            WHEN m.to_location_id IS NULL THEN l_from.location_code
            ELSE NULL
        END::text AS location_code,
        1::bigint AS product_count,
        m.quantity::numeric AS total_quantity,
        NULL::text AS external_reference
    FROM period_movements m
    LEFT JOIN wms.locations l_from ON l_from.location_id = m.from_location_id
    LEFT JOIN wms.locations l_to ON l_to.location_id = m.to_location_id
    WHERE NOT EXISTS (
            SELECT 1 FROM unambiguous_fbs_movement_ids f
            WHERE f.movement_id = m.movement_id)
),
all_events AS (
    SELECT * FROM kit_events
    UNION ALL
    SELECT * FROM re_sorting_events
    UNION ALL
    SELECT * FROM fbs_events
    UNION ALL
    SELECT * FROM standalone_movement_events
)
"""

COUNT_OPERATIONS_HISTORY = (
    _EVENTS_CTES
    + """
SELECT count(*)::bigint FROM all_events
"""
)

GET_OPERATIONS_HISTORY = (
    _EVENTS_CTES
    + """
SELECT event_id, source_type, operation_type, operation_name, status,
       created_at, completed_at, author, location_id, location_code,
       product_count, total_quantity, external_reference
FROM all_events
ORDER BY created_at DESC, event_id DESC
LIMIT $9 OFFSET $10
"""
)

# === DETAIL: static source-specific queries ===
GET_KIT_OPERATION_HEADER = """SELECT ko.operation_id,ko.operation_type,ko.kit_product_id,p.name AS kit_product_name,ko.quantity,ko.operation_location_id,ko.location_id,COALESCE(ko.location_code,l.location_code) AS location_code,ko.author,ko.status,ko.created_at,ko.completed_at FROM wms.kit_operations ko LEFT JOIN public.products p ON p.id=ko.kit_product_id LEFT JOIN wms.locations l ON l.location_id=ko.location_id WHERE ko.operation_id=$1"""
GET_KIT_ITEMS_WITH_MOVEMENTS = """SELECT i.item_id,i.role,i.product_id,p.name AS product_name,i.quantity_per_kit,i.total_quantity,i.movement_id,i.movement_created_at,m.movement_id AS candidate_movement_id,m.movement_type AS candidate_movement_type,m.product_id AS candidate_product_id,mp.name AS candidate_product_name,m.quantity AS candidate_quantity,m.from_location_id AS candidate_from_location_id,lf.location_code AS candidate_from_location_code,m.to_location_id AS candidate_to_location_id,lt.location_code AS candidate_to_location_code,m.batch_number AS candidate_batch_number,m.container_code AS candidate_container_code,m.user_name AS candidate_user_name,m.reason AS candidate_reason,m.source_type AS candidate_source_type,m.source_id AS candidate_source_id,m.source_item_id AS candidate_source_item_id,m.metadata AS candidate_metadata,m.created_at AS candidate_created_at FROM wms.kit_operation_items i LEFT JOIN public.products p ON p.id=i.product_id LEFT JOIN wms.movements m ON m.movement_id=i.movement_id AND m.created_at=i.movement_created_at LEFT JOIN public.products mp ON mp.id=m.product_id LEFT JOIN wms.locations lf ON lf.location_id=m.from_location_id LEFT JOIN wms.locations lt ON lt.location_id=m.to_location_id WHERE i.operation_id=$1 ORDER BY i.item_id,m.created_at,m.movement_id"""
GET_RE_SORTING_HEADER = """SELECT ro.operation_id,ro.from_product_id,fp.name AS from_product_name,ro.to_product_id,tp.name AS to_product_name,ro.quantity,ro.operation_location_id,ro.location_id,COALESCE(ro.location_code,l.location_code) AS location_code,ro.reason,ro.author,ro.status,ro.created_at,ro.completed_at FROM wms.re_sorting_operations ro LEFT JOIN public.products fp ON fp.id=ro.from_product_id LEFT JOIN public.products tp ON tp.id=ro.to_product_id LEFT JOIN wms.locations l ON l.location_id=ro.location_id WHERE ro.operation_id=$1"""
GET_RE_SORTING_ITEMS_WITH_MOVEMENTS = """SELECT i.item_id,i.role,i.product_id,p.name AS product_name,i.quantity,i.movement_id,i.movement_created_at,m.movement_id AS candidate_movement_id,m.movement_type AS candidate_movement_type,m.product_id AS candidate_product_id,mp.name AS candidate_product_name,m.quantity AS candidate_quantity,m.from_location_id AS candidate_from_location_id,lf.location_code AS candidate_from_location_code,m.to_location_id AS candidate_to_location_id,lt.location_code AS candidate_to_location_code,m.batch_number AS candidate_batch_number,m.container_code AS candidate_container_code,m.user_name AS candidate_user_name,m.reason AS candidate_reason,m.source_type AS candidate_source_type,m.source_id AS candidate_source_id,m.source_item_id AS candidate_source_item_id,m.metadata AS candidate_metadata,m.created_at AS candidate_created_at FROM wms.re_sorting_operation_items i LEFT JOIN public.products p ON p.id=i.product_id LEFT JOIN wms.movements m ON m.movement_id=i.movement_id AND m.created_at=i.movement_created_at LEFT JOIN public.products mp ON mp.id=m.product_id LEFT JOIN wms.locations lf ON lf.location_id=m.from_location_id LEFT JOIN wms.locations lt ON lt.location_id=m.to_location_id WHERE i.operation_id=$1 ORDER BY i.item_id,m.created_at,m.movement_id"""
GET_FBS_SHIPMENT_HEADER = """SELECT shipment_id,source,status,received_at,completed_at,total_items,error_message,raw_message FROM wms.fbs_shipments WHERE shipment_id=$1"""
GET_FBS_SHIPMENT_ITEMS = """SELECT i.item_id,i.product_id,p.name AS product_name,i.quantity,i.author,i.supply_id,i.account,i.assembly_tasks,i.warehouse_id,i.delivery_type,i.wb_warehouse,i.shipment_date,i.status,i.error_message,i.retry_count,i.max_retries,i.next_retry_at,i.movement_id,i.created_at,i.updated_at FROM wms.fbs_shipment_items i LEFT JOIN public.products p ON p.id=i.product_id WHERE i.shipment_id=$1 ORDER BY i.item_id"""
GET_MOVEMENTS_BY_IDS = """SELECT m.movement_id,m.movement_type,m.product_id,p.name AS product_name,m.quantity,m.from_location_id,lf.location_code AS from_location_code,m.to_location_id,lt.location_code AS to_location_code,m.batch_number,m.container_code,m.user_name,m.reason,m.source_type,m.source_id,m.source_item_id,m.metadata,m.created_at FROM wms.movements m LEFT JOIN public.products p ON p.id=m.product_id LEFT JOIN wms.locations lf ON lf.location_id=m.from_location_id LEFT JOIN wms.locations lt ON lt.location_id=m.to_location_id WHERE m.movement_id=ANY($1::bigint[]) ORDER BY m.movement_id,m.created_at"""
GET_MOVEMENT_BY_IDENTITY = """SELECT m.movement_id,m.movement_type,m.product_id,p.name AS product_name,m.quantity,m.from_location_id,lf.location_code AS from_location_code,m.to_location_id,lt.location_code AS to_location_code,m.batch_number,m.container_code,m.user_name,m.reason,m.source_type,m.source_id,m.source_item_id,m.metadata,m.created_at FROM wms.movements m LEFT JOIN public.products p ON p.id=m.product_id LEFT JOIN wms.locations lf ON lf.location_id=m.from_location_id LEFT JOIN wms.locations lt ON lt.location_id=m.to_location_id WHERE m.movement_id=$1 AND m.created_at=$2::timestamptz ORDER BY m.created_at"""
