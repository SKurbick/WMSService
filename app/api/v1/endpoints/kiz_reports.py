"""Отдельная read-only аналитика реестра КИЗ."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import get_kiz_report_service
from app.core.schemas.kiz_reports import KizSummaryQuery, KizSummaryResponse
from app.core.services.kiz_report_service import KizReportService

router = APIRouter(prefix="/kiz-reports", tags=["Аналитика КИЗ"])

COUNTS_EXAMPLE = {
    "total_count": 150,
    "registered_count": 30,
    "active_count": 90,
    "active_loose_count": 70,
    "active_container_count": 20,
    "shipped_count": 20,
    "error_count": 6,
    "deactivated_count": 4,
}
SUMMARY_EXAMPLE = {
    "group_by": ["organization", "product_id"],
    "filters": {
        "organization": "СТАРТ",
        "organization_missing": None,
        "product_id": "testwild",
        "lifecycle_status": None,
        "location_code": None,
        "container_id": None,
        "container_qr_code": None,
        "origin_type": None,
        "origin_reference": None,
    },
    "total_groups": 1,
    "limit": 50,
    "offset": 0,
    "totals": COUNTS_EXAMPLE,
    "items": [
        {"group": {"organization": "СТАРТ", "product_id": "testwild"}, "counts": COUNTS_EXAMPLE}
    ],
}


@router.get(
    "/summary",
    response_model=KizSummaryResponse,
    response_model_exclude_unset=True,
    summary="Получить сводку КИЗ по организациям и другим измерениям",
    description=(
        "Read-only сводка существующих identity из wms.kiz. Все параметры необязательны. "
        "По умолчанию группировка по organization и product_id, все lifecycle, limit=50, offset=0. "
        "group_by можно повторять либо передать через запятую, например organization,product_id. "
        "Разрешены organization, product_id, lifecycle_status, location_code, container_id, "
        "container_qr_code, origin_type; дубликаты измерений запрещены.\n\n"
        "organization_missing=true выбирает только organization=NULL, false — только заполненную "
        "организацию, отсутствие параметра — все записи. organization и organization_missing=true "
        "несовместимы (422). Остальные фильтры соединяются через AND, сравнение строк точное. "
        "Неизвестный товар, адрес или контейнер возвращает 200 с пустым items и нулевыми totals.\n\n"
        "registered_count — коды без подтверждённого физического holder; они не резервируют остаток. "
        "active_count — действующие идентифицированные единицы; active_loose_count и "
        "active_container_count показывают их размещение. shipped/error/deactivated считаются "
        "отдельно. total_count включает все выбранные состояния. Сообщения очереди без регистрации "
        "в wms.kiz не учитываются. Общий физический остаток и unidentified по организации не вычисляются.\n\n"
        "Локация — текущий прямой holder либо текущий адрес контейнера, без дочерних адресов "
        "и восстановления исторических мест. У registered/shipped holder отсутствует. У error/deactivated "
        "сохранённый адрес не подтверждает физическое размещение. В group "
        "возвращаются только выбранные измерения, включая явные null. Пагинация применяется к "
        "группам; totals и total_groups относятся ко всему отфильтрованному набору. Сортировка "
        "по group_by по возрастанию, NULL сначала. Весь ответ читается в одном read-only "
        "REPEATABLE READ снимке; остатки, события и состояния КИЗ не изменяются."
    ),
    responses={
        200: {
            "description": "Согласованная сводка и страница групп, включая пустой результат.",
            "content": {"application/json": {"example": SUMMARY_EXAMPLE}},
        },
        422: {
            "description": "Неверные фильтры, группировки, пагинация или несовместимые параметры организации.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["detail"],
                        "properties": {
                            "detail": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "loc": {
                                            "type": "array",
                                            "items": {
                                                "anyOf": [{"type": "string"}, {"type": "integer"}]
                                            },
                                        },
                                        "msg": {"type": "string"},
                                        "type": {"type": "string"},
                                    },
                                    "required": ["loc", "msg", "type"],
                                },
                            }
                        },
                    }
                }
            },
        },
        500: {
            "description": "Ошибка БД или внутреннего сервиса; частичная сводка не возвращается.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "detail": {"type": "string"},
                            "message": {"type": "string"},
                            "error_code": {"type": "string"},
                        },
                        "required": ["detail", "message", "error_code"],
                    },
                    "example": {
                        "detail": "Внутренняя ошибка сервера",
                        "message": "Внутренняя ошибка сервера",
                        "error_code": "INTERNAL_ERROR",
                    },
                }
            },
        },
    },
)
async def get_kiz_summary(
    query: Annotated[KizSummaryQuery, Query()],
    service: KizReportService = Depends(get_kiz_report_service),
):
    return await service.summary(query)
