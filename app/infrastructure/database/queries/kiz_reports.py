"""Whitelisted group dimensions and parameterized filters for KIZ reports."""

DIMENSIONS = {
    "organization": "organization",
    "product_id": "product_id",
    "lifecycle_status": "lifecycle_status",
    "location_code": "location_code",
    "container_id": "container_id",
    "container_qr_code": "container_qr_code",
    "origin_type": "origin_type",
}

FILTERED = """
WITH filtered AS (
    SELECT k.organization, k.product_id, k.lifecycle_status, k.location_id,
           k.container_id, k.origin_type, c.qr_code AS container_qr_code,
           COALESCE(loose_location.location_code, container_location.location_code) AS location_code
    FROM wms.kiz k
    LEFT JOIN wms.locations loose_location ON loose_location.location_id = k.location_id
    LEFT JOIN wms.containers c ON c.container_id = k.container_id
    LEFT JOIN wms.locations container_location ON container_location.location_id = c.location_id
    WHERE ($1::text IS NULL OR k.organization = $1)
      AND ($2::boolean IS NULL OR (k.organization IS NULL) = $2)
      AND ($3::text IS NULL OR k.product_id = $3)
      AND ($4::text IS NULL OR k.lifecycle_status = $4)
      AND ($5::text IS NULL OR COALESCE(loose_location.location_code, container_location.location_code) = $5)
      AND ($6::bigint IS NULL OR k.container_id = $6)
      AND ($7::text IS NULL OR c.qr_code = $7)
      AND ($8::text IS NULL OR k.origin_type = $8)
      AND ($9::text IS NULL OR k.origin_reference = $9)
)
"""

COUNTS = """
COUNT(*) AS total_count,
COUNT(*) FILTER (WHERE lifecycle_status = 'registered') AS registered_count,
COUNT(*) FILTER (WHERE lifecycle_status = 'active') AS active_count,
COUNT(*) FILTER (WHERE lifecycle_status = 'active' AND location_id IS NOT NULL AND container_id IS NULL) AS active_loose_count,
COUNT(*) FILTER (WHERE lifecycle_status = 'active' AND container_id IS NOT NULL AND location_id IS NULL) AS active_container_count,
COUNT(*) FILTER (WHERE lifecycle_status = 'shipped') AS shipped_count,
COUNT(*) FILTER (WHERE lifecycle_status = 'error') AS error_count,
COUNT(*) FILTER (WHERE lifecycle_status = 'deactivated') AS deactivated_count
"""

TOTALS = FILTERED + "SELECT " + COUNTS + " FROM filtered"


def grouped_queries(group_by):
    columns = [DIMENSIONS[dimension] for dimension in group_by]
    selection = ", ".join(columns)
    ordering = ", ".join(column + " ASC NULLS FIRST" for column in columns)
    count = FILTERED + f"SELECT COUNT(*) FROM (SELECT 1 FROM filtered GROUP BY {selection}) groups"
    page = (
        FILTERED
        + f"SELECT {selection}, {COUNTS} FROM filtered GROUP BY {selection} ORDER BY {ordering} LIMIT $10 OFFSET $11"
    )
    return count, page
