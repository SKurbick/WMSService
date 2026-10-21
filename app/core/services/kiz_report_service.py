"""Consistent read-only snapshot for KIZ summary counters and groups."""

from app.core.schemas.kiz_reports import (
    KizSummaryCounts,
    KizSummaryFilters,
    KizSummaryGroup,
    KizSummaryItem,
    KizSummaryQuery,
    KizSummaryResponse,
)


class KizReportService:
    def __init__(self, repository):
        self.repository = repository

    async def summary(self, query: KizSummaryQuery) -> KizSummaryResponse:
        async with self.repository.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                totals, total_groups, rows = await self.repository.summarize(connection, query)
        items = [
            KizSummaryItem(
                group=KizSummaryGroup(
                    **{dimension: row[dimension] for dimension in query.group_by}
                ),
                counts=KizSummaryCounts(**row),
            )
            for row in rows
        ]
        return KizSummaryResponse(
            group_by=query.group_by,
            filters=KizSummaryFilters.model_validate(query.model_dump()),
            total_groups=total_groups,
            limit=query.limit,
            offset=query.offset,
            totals=KizSummaryCounts(**totals),
            items=items,
        )
