-- Read-only preflight перед добавлением supply.organization в KIZ import и wms.kiz.

SELECT
    to_regclass('wms.kiz_import_messages') AS kiz_import_messages,
    to_regclass('wms.kiz') AS kiz,
    to_regclass('wms.kiz_import_message_kiz') AS kiz_import_message_kiz;

SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'wms'
  AND table_name IN ('kiz_import_messages', 'kiz')
  AND column_name = 'organization'
ORDER BY table_name;

SELECT
    count(*) FILTER (
        WHERE raw_payload->'supply' ? 'organization'
    ) AS messages_with_organization,
    count(*) FILTER (
        WHERE raw_payload->'supply' ? 'organization'
          AND jsonb_typeof(raw_payload->'supply'->'organization') <> 'string'
    ) AS invalid_type,
    count(*) FILTER (
        WHERE jsonb_typeof(raw_payload->'supply'->'organization') = 'string'
          AND NULLIF(btrim(raw_payload->'supply'->>'organization'), '') IS NULL
    ) AS blank_organization
FROM wms.kiz_import_messages;

SELECT
    link.kiz_id,
    count(DISTINCT NULLIF(btrim(message.raw_payload->'supply'->>'organization'), ''))
        AS distinct_organizations
FROM wms.kiz_import_message_kiz AS link
JOIN wms.kiz_import_messages AS message USING (message_id)
WHERE jsonb_typeof(message.raw_payload->'supply'->'organization') = 'string'
  AND NULLIF(btrim(message.raw_payload->'supply'->>'organization'), '') IS NOT NULL
GROUP BY link.kiz_id
HAVING count(DISTINCT NULLIF(btrim(message.raw_payload->'supply'->>'organization'), '')) > 1
ORDER BY link.kiz_id;
