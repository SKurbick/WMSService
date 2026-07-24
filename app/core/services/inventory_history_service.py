"""Сервис дневной истории остатков."""

from datetime import date

from app.core.exceptions import InventoryHistoryValidationError, LocationNotFoundError
from app.core.schemas.inventory_history import (
    DailyBalanceDay,
    DailyBalanceProduct,
    DailyBalancesResponse,
)
from app.infrastructure.database.repositories.inventory_history_repository import (
    InventoryHistoryRepository,
)


class InventoryHistoryService:
    TIMEZONE = "Europe/Moscow"

    def __init__(self, repository: InventoryHistoryRepository):
        self.repository = repository

    async def get_daily_balances(
        self,
        date_from: date,
        date_to: date,
        product_id: str | None = None,
        location_id: int | None = None,
        include_subtree: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> DailyBalancesResponse:
        if date_from > date_to:
            raise InventoryHistoryValidationError("date_from не может быть позже date_to")
        if (date_to - date_from).days + 1 > 366:
            raise InventoryHistoryValidationError("Период не может превышать 366 календарных дней")
        if include_subtree and location_id is None:
            raise InventoryHistoryValidationError(
                "include_subtree=true допустим только вместе с location_id"
            )
        location_exists, total_products, rows = await self.repository.get_daily_balance_page(
            date_from, date_to, product_id, location_id, include_subtree, limit, offset
        )
        if not location_exists:
            raise LocationNotFoundError(f"Локация с ID {location_id} не найдена")
        products: dict[str, DailyBalanceProduct] = {}
        for record in rows:
            row = dict(record)
            item = products.setdefault(
                row["product_id"],
                DailyBalanceProduct(
                    product_id=row["product_id"], product_name=row["product_name"], days=[]
                ),
            )
            item.days.append(
                DailyBalanceDay(
                    date=row["day"],
                    opening_quantity=row["opening_quantity"],
                    incoming_quantity=row["incoming_quantity"],
                    outgoing_quantity=row["outgoing_quantity"],
                    closing_quantity=row["closing_quantity"],
                )
            )
        return DailyBalancesResponse(
            date_from=date_from,
            date_to=date_to,
            timezone=self.TIMEZONE,
            location_id=location_id,
            include_subtree=include_subtree,
            total_products=total_products,
            limit=limit,
            offset=offset,
            items=list(products.values()),
        )
