"""Сервис дневной истории остатков."""

from datetime import date
from decimal import Decimal
from typing import Literal

from app.core.exceptions import InventoryHistoryValidationError, LocationNotFoundError
from app.core.schemas.inventory_history import (
    DailyBalanceDay,
    DailyBalanceProduct,
    DailyBalancesResponse,
    DailyTurnoverDay,
    DailyTurnoverProduct,
    DailyTurnoverResponse,
    MonthlyTurnoverMonth,
    MonthlyTurnoverProduct,
    MonthlyTurnoverResponse,
    TurnoverResponse,
    TurnoverQuantities,
)
from app.infrastructure.database.repositories.inventory_history_repository import (
    InventoryHistoryRepository,
)


class InventoryHistoryService:
    TIMEZONE = "Europe/Moscow"

    def __init__(self, repository: InventoryHistoryRepository):
        self.repository = repository

    async def get_daily_turnover(
        self,
        date_from: date,
        date_to: date,
        product_id: str | None = None,
        group_by: Literal["day", "month"] = "day",
    ) -> TurnoverResponse:
        if group_by not in {"day", "month"}:
            raise InventoryHistoryValidationError("group_by должен быть day или month")
        if date_from > date_to:
            raise InventoryHistoryValidationError("date_from не может быть позже date_to")
        if (date_to - date_from).days + 1 > 366:
            raise InventoryHistoryValidationError("Период не может превышать 366 календарных дней")
        rows = await self.repository.get_daily_turnover(date_from, date_to, product_id, group_by)
        products: dict[str, DailyTurnoverProduct | MonthlyTurnoverProduct] = {}
        for row in rows:
            product = products.get(row["product_id"])
            if product is None:
                fields = dict(
                    product_id=row["product_id"],
                    product_name=row["product_name"],
                    totals=TurnoverQuantities(
                        received_quantity=Decimal(0), shipped_quantity=Decimal(0)
                    ),
                )
                product = (
                    DailyTurnoverProduct(**fields, days=[])
                    if group_by == "day"
                    else MonthlyTurnoverProduct(**fields, months=[])
                )
                products[row["product_id"]] = product
            quantities = dict(
                received_quantity=row["received_quantity"], shipped_quantity=row["shipped_quantity"]
            )
            if isinstance(product, DailyTurnoverProduct):
                entry = DailyTurnoverDay(date=row["period_date"], **quantities)
                product.days.append(entry)
            else:
                entry = MonthlyTurnoverMonth(month=row["period_date"], **quantities)
                product.months.append(entry)
            product.totals.received_quantity += entry.received_quantity
            product.totals.shipped_quantity += entry.shipped_quantity
        response_model = DailyTurnoverResponse if group_by == "day" else MonthlyTurnoverResponse
        return response_model(
            date_from=date_from,
            date_to=date_to,
            timezone=self.TIMEZONE,
            total_products=len(products),
            items=list(products.values()),
        )

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
