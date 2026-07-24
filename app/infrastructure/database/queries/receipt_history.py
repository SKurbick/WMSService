"""Static SQL истории документа поступления."""

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
