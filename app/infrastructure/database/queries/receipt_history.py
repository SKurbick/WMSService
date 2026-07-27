"""Static SQL истории документа поступления."""

LEGACY_REVISION_KEY_SQL = "COALESCE(update_document_datetime, document_created_at, supply_date)"
LEGACY_FALLBACK_ID_SQL = """CASE WHEN update_document_datetime IS NULL
    AND document_created_at IS NULL AND supply_date IS NULL THEN id END"""

GET_CURRENT_SNAPSHOT = """
SELECT ri.*, p.name AS product_name
FROM wms.receipt_items ri
LEFT JOIN public.products p ON p.id = ri.product_id
WHERE ri.guid = $1
ORDER BY ri.product_id, ri.receipt_item_id
"""

COUNT_REVISIONS = """
SELECT count(*)
FROM (
    SELECT COALESCE(update_document_datetime, document_created_at, supply_date) AS revision_key_at,
           CASE WHEN update_document_datetime IS NULL AND document_created_at IS NULL
                     AND supply_date IS NULL THEN id END AS fallback_id
    FROM public.supply_to_sellers_warehouse
    WHERE guid = $1
    GROUP BY 1, 2
) revisions
"""

GET_REVISION_HEADERS = """
WITH source_rows AS (
    SELECT s.*,
           COALESCE(update_document_datetime, document_created_at, supply_date) AS revision_key_at,
           CASE WHEN update_document_datetime IS NULL AND document_created_at IS NULL
                     AND supply_date IS NULL THEN id END AS fallback_id
    FROM public.supply_to_sellers_warehouse s
    WHERE guid = $1
), revision_keys AS (
    SELECT revision_key_at, fallback_id, max(id) AS max_legacy_row_id,
           bool_or(is_valid IS TRUE) AS is_current
    FROM source_rows
    GROUP BY revision_key_at, fallback_id
), paged AS (
    SELECT * FROM revision_keys
    ORDER BY is_current DESC, revision_key_at DESC NULLS LAST, max_legacy_row_id DESC
    LIMIT $2 OFFSET $3
)
SELECT p.revision_key_at,
       p.revision_key_at AT TIME ZONE 'Europe/Moscow' AS revision_at,
       p.fallback_id, p.max_legacy_row_id, p.is_current,
       h.document_number,
       h.document_created_at AT TIME ZONE 'Europe/Moscow' AS document_created_at,
       h.supply_date AT TIME ZONE 'Europe/Moscow' AS supply_date,
       h.update_document_datetime AT TIME ZONE 'Europe/Moscow' AS update_document_datetime,
       h.event_status, h.supplier_name, h.supplier_code, h.author_of_the_change,
       h.our_organizations_name, h.order_guid, h.currency,
       h.invoice_number, h.transport_number
FROM paged p
JOIN source_rows h ON h.id = p.max_legacy_row_id
ORDER BY p.is_current DESC, p.revision_key_at DESC NULLS LAST, p.max_legacy_row_id DESC
"""

GET_REVISION_ITEMS = """
SELECT s.id AS legacy_row_id,
       COALESCE(s.update_document_datetime, s.document_created_at, s.supply_date) AS revision_key_at,
       CASE WHEN s.update_document_datetime IS NULL AND s.document_created_at IS NULL
                 AND s.supply_date IS NULL THEN s.id END AS fallback_id,
       s.local_vendor_code AS product_id,
       COALESCE(p.name, s.product_name) AS product_name,
       s.quantity, s.amount_with_vat, s.amount_without_vat, s.planned_cost,
       s.pack_count, s.pack_multiplicity, s.is_valid
FROM public.supply_to_sellers_warehouse s
LEFT JOIN public.products p ON p.id = s.local_vendor_code
WHERE s.guid = $1
  AND (
      COALESCE(s.update_document_datetime, s.document_created_at, s.supply_date)
          = ANY($2::timestamp without time zone[])
      OR (s.update_document_datetime IS NULL AND s.document_created_at IS NULL
          AND s.supply_date IS NULL AND s.id = ANY($3::integer[]))
  )
ORDER BY s.local_vendor_code, s.id
"""

RECEIPT_LIST_CTES = f"""
WITH params AS (
    SELECT $1::date AS date_from, $2::date AS date_to,
           $1::date::timestamp AT TIME ZONE 'Europe/Moscow' AS start_at,
           ($2::date + 1)::timestamp AT TIME ZONE 'Europe/Moscow' AS end_at
), legacy_source_rows AS (
    SELECT s.*,
           {LEGACY_REVISION_KEY_SQL} AS revision_key_at,
           {LEGACY_FALLBACK_ID_SQL} AS fallback_id
    FROM public.supply_to_sellers_warehouse s
), legacy_revision_keys AS (
    SELECT guid, revision_key_at, fallback_id, max(id) AS max_legacy_row_id,
           bool_or(is_valid IS TRUE) AS is_current,
           count(*)::bigint AS item_count,
           count(DISTINCT local_vendor_code)
               FILTER (WHERE local_vendor_code IS NOT NULL)::bigint AS product_count,
           COALESCE(sum(quantity), 0::numeric) AS total_quantity,
           bool_or(local_vendor_code = $11::varchar) AS has_filtered_product
    FROM legacy_source_rows
    GROUP BY guid, revision_key_at, fallback_id
), legacy_guids AS (
    SELECT DISTINCT guid FROM legacy_source_rows
), snapshot_ranked AS (
    SELECT ri.*,
           row_number() OVER (
               PARTITION BY guid ORDER BY updated_at DESC, receipt_item_id DESC
           ) AS header_rank
    FROM wms.receipt_items ri
), snapshot_totals AS (
    SELECT guid, count(*)::bigint AS item_count,
           count(DISTINCT product_id)
               FILTER (WHERE product_id IS NOT NULL)::bigint AS product_count,
           COALESCE(sum(quantity), 0::numeric) AS total_quantity,
           max(updated_at) AS snapshot_updated_at,
           bool_or(product_id = $11::varchar) AS has_filtered_product
    FROM wms.receipt_items
    GROUP BY guid
), snapshot_headers AS (
    SELECT r.guid, r.document_number, r.document_created_at, r.supply_date,
           r.update_document_datetime, r.event_status, r.supplier_name,
           r.supplier_code, r.author_of_the_change, r.our_organizations_name,
           r.order_guid, r.currency,
           COALESCE(r.update_document_datetime, r.document_created_at, r.supply_date,
                    r.updated_at, r.created_at) AS snapshot_revision_at,
           t.item_count, t.product_count, t.total_quantity, t.snapshot_updated_at,
           t.has_filtered_product
    FROM snapshot_ranked r
    JOIN snapshot_totals t USING (guid)
    WHERE r.header_rank = 1
), legacy_events AS (
    SELECT 'legacy_revision'::text AS source_type, k.guid, k.revision_key_at,
           k.fallback_id, k.max_legacy_row_id,
           k.revision_key_at AT TIME ZONE 'Europe/Moscow' AS revision_at,
           k.is_current, (sh.guid IS NOT NULL) AS has_current_snapshot,
           sh.snapshot_updated_at,
           h.document_number,
           h.document_created_at AT TIME ZONE 'Europe/Moscow' AS document_created_at,
           h.supply_date AT TIME ZONE 'Europe/Moscow' AS supply_date,
           h.update_document_datetime AT TIME ZONE 'Europe/Moscow'
               AS update_document_datetime,
           h.event_status, h.supplier_name, h.supplier_code,
           h.author_of_the_change, h.our_organizations_name, h.order_guid,
           h.currency, h.invoice_number, h.transport_number,
           k.item_count, k.product_count, k.total_quantity, k.has_filtered_product
    FROM legacy_revision_keys k
    JOIN legacy_source_rows h ON h.id = k.max_legacy_row_id
    LEFT JOIN snapshot_headers sh ON sh.guid = k.guid
), wms_only_snapshot_events AS (
    SELECT 'wms_snapshot_only'::text AS source_type, sh.guid,
           NULL::timestamp AS revision_key_at, NULL::integer AS fallback_id,
           NULL::integer AS max_legacy_row_id, sh.snapshot_revision_at AS revision_at,
           true AS is_current, true AS has_current_snapshot, sh.snapshot_updated_at,
           sh.document_number, sh.document_created_at, sh.supply_date,
           sh.update_document_datetime, sh.event_status, sh.supplier_name,
           sh.supplier_code, sh.author_of_the_change, sh.our_organizations_name,
           sh.order_guid, sh.currency, NULL::varchar AS invoice_number,
           NULL::varchar AS transport_number, sh.item_count, sh.product_count,
           sh.total_quantity, sh.has_filtered_product
    FROM snapshot_headers sh
    LEFT JOIN legacy_guids lg ON lg.guid = sh.guid
    WHERE lg.guid IS NULL
), all_receipt_events AS (
    SELECT * FROM legacy_events
    UNION ALL
    SELECT * FROM wms_only_snapshot_events
), filtered_events AS (
    SELECT e.*,
           CASE
             WHEN e.source_type = 'wms_snapshot_only' THEN
               'receipt_snapshot:' ||
               rtrim(translate(encode(convert_to(e.guid, 'UTF8'), 'base64'),
                               '+/', '-_'), '=')
             WHEN e.revision_at IS NULL THEN
               'receipt_revision:' ||
               rtrim(translate(encode(convert_to(e.guid, 'UTF8'), 'base64'),
                               '+/', '-_'), '=') ||
               ':legacy:' || e.fallback_id::text
             ELSE
               'receipt_revision:' ||
               rtrim(translate(encode(convert_to(e.guid, 'UTF8'), 'base64'),
                               '+/', '-_'), '=') || ':' ||
               floor(extract(epoch FROM e.revision_at) * 1000000)::bigint::text
           END AS row_sort_id
    FROM all_receipt_events e
    CROSS JOIN params p
    WHERE (
        (e.revision_at >= p.start_at AND e.revision_at < p.end_at)
        OR ($13::boolean AND e.source_type = 'legacy_revision'
            AND e.revision_at IS NULL)
    )
      AND ($3::varchar IS NULL OR e.source_type = $3)
      AND ($4::varchar IS NULL OR e.guid = $4)
      AND ($5::varchar IS NULL OR e.document_number = $5)
      AND ($6::varchar IS NULL OR e.supplier_name = $6)
      AND ($7::varchar IS NULL OR e.supplier_code = $7)
      AND ($8::varchar IS NULL OR e.event_status = $8)
      AND ($9::varchar IS NULL OR e.author_of_the_change = $9)
      AND ($10::varchar IS NULL OR e.order_guid = $10)
      AND ($11::varchar IS NULL OR e.has_filtered_product)
      AND ($12::boolean IS NULL OR e.is_current = $12)
)
"""

COUNT_RECEIPT_HISTORY_LIST = (
    RECEIPT_LIST_CTES
    + """
SELECT count(*)::bigint AS total, count(DISTINCT guid)::bigint AS total_documents
FROM filtered_events
"""
)

GET_RECEIPT_HISTORY_LIST = (
    RECEIPT_LIST_CTES
    + """
SELECT *
FROM filtered_events
ORDER BY revision_at DESC NULLS LAST, guid ASC,
         row_sort_id DESC
LIMIT $14 OFFSET $15
"""
)
