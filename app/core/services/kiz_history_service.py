"""Build the read-only KIZ history model from existing ledgers."""

from app.core.kiz_errors import KizNotFoundError
from app.core.schemas.kiz_history import KizHistory


class KizHistoryIntegrityError(RuntimeError):
    """A linked physical history row violates database assumptions."""


class KizHistoryService:
    def __init__(self, repository):
        self.repository = repository

    async def get(self, kiz_code: str) -> KizHistory:
        async with self.repository.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                current = await self.repository.current_state(conn, kiz_code)
                if current is None:
                    raise KizNotFoundError(f"КИЗ {kiz_code} не найден")

                rows = await self.repository.timeline(conn, current["kiz_id"])
                timeline = []
                for record in rows:
                    row = dict(record)
                    movement_ref = row["movement_ref"]
                    if row.pop("registry_missing") or row.pop("movement_missing"):
                        raise KizHistoryIntegrityError(
                            "Нарушена связь KIZ movement history: "
                            f"kiz_id={current['kiz_id']}, movement_ref={movement_ref}"
                        )
                    movement_type = row.pop("movement_type")
                    shipped_event_count = row.pop("shipped_event_count")
                    if shipped_event_count > 1:
                        raise KizHistoryIntegrityError(
                            "Для одного KIZ movement найдено несколько shipped events: "
                            f"kiz_id={current['kiz_id']}, movement_ref={movement_ref}"
                        )
                    if shipped_event_count and movement_type != "ship":
                        raise KizHistoryIntegrityError(
                            "Shipped lifecycle event связан не с ship movement: "
                            f"kiz_id={current['kiz_id']}, movement_ref={movement_ref}"
                        )
                    row.pop("source_rank")
                    row.pop("source_identity")
                    timeline.append(row)

                state = dict(current)
                return KizHistory(
                    kiz_code=state["kiz_code"],
                    product_id=state["product_id"],
                    current_state=state,
                    timeline=timeline,
                )
