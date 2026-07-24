"""SQL дневной истории остатков, восстанавливаемой только из movements."""

LOCATION_EXISTS = "SELECT EXISTS(SELECT 1 FROM wms.locations WHERE location_id = $1)"

_LEDGER_CTES = """
WITH params AS (
    SELECT $1::date AS date_from, $2::date AS date_to,
           ($1::date::timestamp AT TIME ZONE 'Europe/Moscow') AS period_start,
           (($2::date + 1)::timestamp AT TIME ZONE 'Europe/Moscow') AS period_end
),
root_location AS (
    SELECT path FROM wms.locations WHERE location_id = $4::bigint
),
scope_locations AS (
    SELECT l.location_id
    FROM wms.locations l
    LEFT JOIN root_location root ON TRUE
    WHERE $4::bigint IS NULL
       OR (CASE WHEN $5::boolean THEN l.path <@ root.path ELSE l.location_id = $4 END)
),
ledger AS (
    SELECT m.product_id, m.created_at, m.quantity::numeric AS quantity,
           m.quantity::numeric AS delta, TRUE AS is_incoming
    FROM wms.movements m
    JOIN scope_locations scope ON scope.location_id = m.to_location_id
    CROSS JOIN params p
    WHERE m.created_at < p.period_end
      AND ($3::varchar IS NULL OR m.product_id = $3)
    UNION ALL
    SELECT m.product_id, m.created_at, m.quantity::numeric AS quantity,
           -m.quantity::numeric AS delta, FALSE AS is_incoming
    FROM wms.movements m
    JOIN scope_locations scope ON scope.location_id = m.from_location_id
    CROSS JOIN params p
    WHERE m.created_at < p.period_end
      AND ($3::varchar IS NULL OR m.product_id = $3)
),
opening_by_product AS (
    SELECT l.product_id, sum(l.delta)::numeric AS opening_quantity
    FROM ledger l CROSS JOIN params p
    WHERE l.created_at < p.period_start
    GROUP BY l.product_id
),
daily_activity AS (
    SELECT l.product_id,
           (l.created_at AT TIME ZONE 'Europe/Moscow')::date AS day,
           COALESCE(sum(l.quantity) FILTER (WHERE l.is_incoming), 0)::numeric
               AS incoming_quantity,
           COALESCE(sum(l.quantity) FILTER (WHERE NOT l.is_incoming), 0)::numeric
               AS outgoing_quantity,
           sum(l.delta)::numeric AS net_quantity
    FROM ledger l CROSS JOIN params p
    WHERE l.created_at >= p.period_start AND l.created_at < p.period_end
    GROUP BY l.product_id, (l.created_at AT TIME ZONE 'Europe/Moscow')::date
),
eligible_products AS (
    SELECT product_id FROM opening_by_product WHERE opening_quantity <> 0
    UNION
    SELECT product_id FROM daily_activity
)
"""

COUNT_DAILY_BALANCE_PRODUCTS = (
    _LEDGER_CTES
    + """
SELECT count(*)::integer FROM eligible_products
"""
)

GET_DAILY_BALANCES = (
    _LEDGER_CTES
    + """
, paged_products AS (
    SELECT ep.product_id, p.name AS product_name,
           COALESCE(obp.opening_quantity, 0)::numeric AS initial_opening
    FROM eligible_products ep
    LEFT JOIN opening_by_product obp ON obp.product_id = ep.product_id
    LEFT JOIN public.products p ON p.id = ep.product_id
    ORDER BY ep.product_id ASC
    LIMIT $6 OFFSET $7
),
calendar_days AS (
    SELECT generate_series(p.date_from, p.date_to, interval '1 day')::date AS day
    FROM params p
),
daily_grid AS (
    SELECT pp.product_id, pp.product_name, pp.initial_opening, cd.day,
           COALESCE(da.incoming_quantity, 0)::numeric AS incoming_quantity,
           COALESCE(da.outgoing_quantity, 0)::numeric AS outgoing_quantity,
           COALESCE(da.net_quantity, 0)::numeric AS net_quantity
    FROM paged_products pp
    CROSS JOIN calendar_days cd
    LEFT JOIN daily_activity da ON da.product_id = pp.product_id AND da.day = cd.day
),
calculated AS (
    SELECT product_id, product_name, day, incoming_quantity, outgoing_quantity,
           initial_opening + COALESCE(
               sum(net_quantity) OVER (
                   PARTITION BY product_id ORDER BY day
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ), 0
           ) AS opening_quantity,
           initial_opening + sum(net_quantity) OVER (
               PARTITION BY product_id ORDER BY day
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS closing_quantity
    FROM daily_grid
)
SELECT product_id, product_name, day, opening_quantity::numeric,
       incoming_quantity::numeric, outgoing_quantity::numeric, closing_quantity::numeric
FROM calculated
ORDER BY product_id ASC, day ASC
"""
)
