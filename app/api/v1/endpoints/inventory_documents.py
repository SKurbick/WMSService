"""Inventory documents, final-count freeze workflow and controlled physical apply."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.v1.dependencies import get_inventory_document_service, require_inventory_apply
from app.core.schemas.inventory_documents import (
    POSTGRES_BIGINT_MAX,
    ApplicationCommand,
    ApplicationPreparationResponse,
    ApplicationResultResponse,
    AttemptCommandResponse,
    AttemptResponse,
    Command,
    CountQuantity,
    CreateDocument,
    Decision,
    DocumentResponse,
    DocumentStatus,
    FoundItem,
    FreezeConfirmation,
    FreezeCommandResponse,
    FreezeStatusResponse,
    HistoryEvent,
    ItemCommandResponse,
    ItemResponse,
    Page,
    PreviewResponse,
    ReconciliationResponse,
    ReturnToCount,
    RevisionResponse,
    UpdateDocument,
)
from app.core.services.inventory_document_service import InventoryDocumentService
from app.core.services.inventory_freeze_service import InventoryFreezeService

router = APIRouter(
    prefix="/inventory-documents",
    tags=["Инвентаризация: документы и пересчёты"],
    responses={
        404: {"description": "Документ, строка или попытка не найдены"},
        409: {"description": "Конфликт состояния, версии, области или idempotency key"},
        503: {"description": "Модуль выключен либо запрошено недоступное применение"},
    },
)
Id = Annotated[int, Path(gt=0, le=POSTGRES_BIGINT_MAX)]
Limit = Annotated[int, Query(ge=1, le=1000)]
Offset = Annotated[int, Query(ge=0, le=POSTGRES_BIGINT_MAX)]
Service = Annotated[InventoryDocumentService, Depends(get_inventory_document_service)]


@router.post(
    "", response_model=DocumentResponse, status_code=201, summary="Создать черновик инвентаризации"
)
async def create_document(data: CreateDocument, service: Service):
    """Создаёт самостоятельный draft; физические остатки не изменяются."""
    return await service.create(data)


@router.get("", response_model=Page[DocumentResponse], summary="Получить документы инвентаризации")
async def list_documents(
    service: Service,
    status: DocumentStatus | None = None,
    warehouse_location_id: Annotated[int | None, Query(gt=0, le=POSTGRES_BIGINT_MAX)] = None,
    limit: Limit = 100,
    offset: Offset = 0,
):
    """Возвращает страницу документов с фильтрами состояния и корня склада."""
    return await service.list_documents(status, warehouse_location_id, limit, offset)


@router.get("/{document_id}", response_model=DocumentResponse, summary="Получить документ")
async def get_document(document_id: Id, service: Service):
    """Возвращает состояние, версию, исходные фильтры и счётчики документа."""
    return await service.get(document_id)


@router.patch("/{document_id}", response_model=DocumentResponse, summary="Изменить черновик")
async def update_document(document_id: Id, data: UpdateDocument, service: Service):
    """Изменяет фильтры и заголовок только в draft под проверкой версии."""
    return await service.update(document_id, data)


@router.post(
    "/{document_id}/preview-scope",
    response_model=PreviewResponse,
    summary="Предпросмотр области и положительных строк черновика",
)
async def preview_scope(document_id: Id, service: Service):
    """Показывает текущую область draft без claims, snapshot и изменения версии."""
    return await service.preview(document_id)


@router.get(
    "/{document_id}/scope",
    response_model=list[dict],
    summary="Получить зафиксированные адреса, включая пустые и неактивные",
)
async def fixed_scope(document_id: Id, service: Service):
    """Возвращает неизменяемый состав адресов на момент запуска документа."""
    return await service.fixed_scope(document_id)


@router.post(
    "/{document_id}/start",
    response_model=DocumentResponse,
    summary="Атомарно зафиксировать область, claims и начальный снимок",
)
async def start_document(document_id: Id, data: Command, service: Service):
    """Создаёт согласованный начальный снимок и уникальные claims одной транзакцией."""
    return await service.start(document_id, data)


@router.get(
    "/{document_id}/items",
    response_model=Page[ItemResponse],
    summary="Получить строки открытого пересчёта",
)
async def list_items(document_id: Id, service: Service, limit: Limit = 100, offset: Offset = 0):
    """Возвращает пары SKU/локация; actual NULL отличается от пересчитанного нуля."""
    return await service.page(document_id, "items", limit, offset)


@router.post(
    "/{document_id}/items",
    response_model=ItemCommandResponse,
    status_code=201,
    summary="Добавить найденный товар с начальным expected=0",
)
async def add_item(document_id: Id, data: FoundItem, service: Service):
    """Добавляет новую пару в frozen scope с initial expected=0; поступления учитываются отдельно."""
    return await service.add_found(document_id, data)


@router.post(
    "/{document_id}/items/{item_id}/attempts",
    response_model=AttemptCommandResponse,
    status_code=201,
    summary="Начать новую попытку пересчёта",
)
async def begin_attempt(document_id: Id, item_id: Id, data: Command, service: Service):
    """Начинает новую попытку и фиксирует свидетельства без блокировки движений."""
    return await service.begin_count(document_id, item_id, data)


@router.get(
    "/{document_id}/items/{item_id}/attempts",
    response_model=Page[AttemptResponse],
    summary="Получить неизъятую историю попыток",
)
async def list_attempts(
    document_id: Id, item_id: Id, service: Service, limit: Limit = 100, offset: Offset = 0
):
    """Возвращает все сохранённые попытки строки в порядке их номеров."""
    return await service.page(document_id, "attempts", limit, offset, item_id)


@router.put(
    "/{document_id}/items/{item_id}/attempts/{attempt_id}/progress",
    response_model=AttemptCommandResponse,
    summary="Сохранить прогресс попытки",
)
async def save_progress(
    document_id: Id, item_id: Id, attempt_id: Id, data: CountQuantity, service: Service
):
    """Сохраняет промежуточное количество и audit, не завершая попытку."""
    return await service.save_count(document_id, item_id, attempt_id, data, complete=False)


@router.post(
    "/{document_id}/items/{item_id}/attempts/{attempt_id}/complete",
    response_model=AttemptCommandResponse,
    summary="Завершить попытку и сохранить свидетельства",
)
async def complete_attempt(
    document_id: Id, item_id: Id, attempt_id: Id, data: CountQuantity, service: Service
):
    """Завершает попытку и отмечает изменения между наблюдениями."""
    return await service.save_count(document_id, item_id, attempt_id, data, complete=True)


@router.post(
    "/{document_id}/reconcile",
    response_model=ReconciliationResponse,
    summary="Создать предварительную ревизию без физических корректировок",
)
async def reconcile(document_id: Id, data: Command, service: Service):
    """Сохраняет предварительную ревизию; физические корректировки не применяются."""
    return await service.reconcile(document_id, data)


@router.post(
    "/{document_id}/submit",
    response_model=ReconciliationResponse,
    summary="Отправить пересчитанный документ на согласование целиком",
)
async def submit(document_id: Id, data: Command, service: Service):
    """Проверяет полноту пересчёта и отправляет весь документ с новой ревизией."""
    return await service.submit(document_id, data)


@router.post(
    "/{document_id}/return-to-count",
    response_model=DocumentResponse,
    summary="Вернуть документ на пересчёт, сохранив попытки и решение",
)
async def return_to_count(document_id: Id, data: ReturnToCount, service: Service):
    """Возвращает документ на пересчёт всех либо affected строк, сохраняя результаты."""
    return await service.return_to_count(document_id, data)


@router.post(
    "/{document_id}/close-without-adjustment",
    response_model=DocumentResponse,
    summary="Окончательно закрыть без применения, освободив claims",
)
async def close_document(document_id: Id, data: Decision, service: Service):
    """Закрывает без применения из draft/counting/pending и освобождает claims атомарно."""
    return await service.close(document_id, data)


@router.get(
    "/{document_id}/history",
    response_model=Page[HistoryEvent],
    summary="Получить историю команд, решений и прогресса",
)
async def history(document_id: Id, service: Service, limit: Limit = 100, offset: Offset = 0):
    """Возвращает неизменяемые команды, прогресс, переходы и решения."""
    return await service.page(document_id, "history", limit, offset)


@router.get(
    "/{document_id}/revisions",
    response_model=Page[RevisionResponse],
    summary="Получить сохранённые ревизии и исходные свидетельства",
)
async def revisions(document_id: Id, service: Service, limit: Limit = 100, offset: Offset = 0):
    """Возвращает историю начального snapshot и предварительных ревизий."""
    return await service.page(document_id, "revisions", limit, offset)


@router.post(
    "/{document_id}/application-preview",
    response_model=ApplicationPreparationResponse,
    summary="Подготовить корректировки отправленной ревизии",
)
async def application_preview(document_id: Id, data: ApplicationCommand, service: Service):
    """Одна неизменяемая подготовка на submitted revision. Физический pilot требует freeze.

    Повтор возвращает сохранённое наблюдение, а не актуализирует его. Для нового
    наблюдения нужны возврат на пересчёт и новая отправка на согласование.
    """
    return await service.prepare_application(document_id, data)


@router.post(
    "/{document_id}/apply",
    response_model=ApplicationResultResponse,
    dependencies=[Depends(require_inventory_apply)],
    summary="Атомарно применить документ через inventory-source adjust",
    responses={
        409: {"description": "Протокол применения недоступен либо конфликт версии/состояния"}
    },
)
async def apply_document(document_id: Id, data: ApplicationCommand, service: Service):
    """Требует flags, финальный пересчёт, готовый freeze и актуальную подготовку."""
    return await service.apply(document_id, data)


@router.get(
    "/{document_id}/application",
    response_model=ApplicationResultResponse,
    summary="Получить подготовку, результат применения и связанные movements",
)
async def application_result(document_id: Id, service: Service):
    """Возвращает историческое наблюдение; оно не является разрешением на применение."""
    return await service.application_result(document_id)


@router.post(
    "/{document_id}/freeze",
    response_model=FreezeCommandResponse,
    summary="Начать адресную блокировку малого partial-документа",
)
async def begin_freeze(document_id: Id, data: Command, service: Service):
    """Блокирует адреса документа; существующие SQL writers должны завершиться до захвата барьеров."""
    return await InventoryFreezeService(service).begin(document_id, data)


@router.get(
    "/{document_id}/freeze",
    response_model=FreezeStatusResponse,
    summary="Состояние блокировки и журнал invalidation",
)
async def freeze_status(document_id: Id, service: Service, limit: Limit = 100, offset: Offset = 0):
    """Показывает состояние, события и связанные незавершённые задачи; timestamps не доказывают физическое время."""
    return await InventoryFreezeService(service).status(document_id, limit, offset)


@router.post(
    "/{document_id}/freeze/confirm",
    response_model=FreezeCommandResponse,
    summary="Подтвердить физическое окно и потребовать финальный пересчёт",
)
async def confirm_freeze(document_id: Id, data: FreezeConfirmation, service: Service):
    """Сохраняет подтверждение ответственного, создаёт epoch и требует новый финальный пересчёт всех строк."""
    return await InventoryFreezeService(service).confirm(document_id, data)


@router.post(
    "/{document_id}/freeze/release",
    response_model=FreezeCommandResponse,
    summary="Явно освободить блокировку без корректировок",
)
async def release_freeze(document_id: Id, data: Decision, service: Service):
    """Освобождает адреса без movements; claims сохраняются до окончательного закрытия документа."""
    return await InventoryFreezeService(service).release(document_id, data)


@router.post(
    "/{document_id}/freeze/ready",
    response_model=FreezeCommandResponse,
    summary="Подтвердить готовность финальной ревизии к применению",
)
async def ready_freeze(document_id: Id, data: ApplicationCommand, service: Service):
    """Повторно проверяет отправленную ревизию, финальные counts и подготовку под адресными барьерами."""
    from app.core.services.inventory_application_service import InventoryApplicationService

    return await InventoryApplicationService(service).ready(document_id, data)
