"""Read-only aggregation of one row per KIZ identity."""

from app.infrastructure.database.queries import kiz_reports as queries


class KizReportRepository:
    def __init__(self, pool):
        self.pool = pool

    async def summarize(self, connection, query):
        arguments = (
            query.organization,
            query.organization_missing,
            query.product_id,
            query.lifecycle_status,
            query.location_code,
            query.container_id,
            query.container_qr_code,
            query.origin_type,
            query.origin_reference,
        )
        count_sql, page_sql = queries.grouped_queries(query.group_by)
        totals = dict(await connection.fetchrow(queries.TOTALS, *arguments))
        total_groups = await connection.fetchval(count_sql, *arguments)
        rows = await connection.fetch(page_sql, *arguments, query.limit, query.offset)
        return totals, total_groups, [dict(row) for row in rows]
