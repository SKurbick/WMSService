"""Inventory stage 1 contracts. No command applies physical stock changes."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.core.json_validation import validate_jsonb_value

POSTGRES_BIGINT_MAX = 2**63 - 1
PositiveBigint = Annotated[int, Field(gt=0, le=POSTGRES_BIGINT_MAX)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Sku = Annotated[str, StringConstraints(min_length=1, max_length=50, pattern=r"^\S(?:.*\S)?$")]
Quantity = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
DocumentStatus = Literal[
    "draft", "counting", "pending_approval", "closed_without_adjustment", "applied"
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def jsonb_text(self):
        validate_jsonb_value(self.model_dump())
        return self


class LocationSelection(Input):
    location_id: PositiveBigint
    include_descendants: bool = True


class DocumentScope(Input):
    title: str = Field(min_length=1, max_length=200, pattern=r"\S")
    mode: Literal["full", "partial"]
    warehouse_location_id: PositiveBigint
    locations: list[LocationSelection] = Field(default_factory=list, max_length=1000)
    product_ids: list[Sku] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.mode == "full" and (self.locations or self.product_ids):
            raise ValueError("Full inventory cannot have partial filters")
        if self.mode == "partial" and not (self.locations or self.product_ids):
            raise ValueError("Partial inventory requires locations or product_ids")
        if len({row.location_id for row in self.locations}) != len(self.locations):
            raise ValueError("Duplicate location selection")
        self.product_ids = sorted(set(self.product_ids))
        self.locations = sorted(self.locations, key=lambda row: row.location_id)
        return self


class CreateDocument(DocumentScope):
    idempotency_key: UUID
    user_name: Name


class Command(Input):
    idempotency_key: UUID
    expected_version: PositiveBigint = Field(description="Optimistic document version")
    user_name: Name


class UpdateDocument(DocumentScope, Command):
    pass


class ApplicationCommand(Command):
    submitted_revision: Annotated[int, Field(gt=0, le=2**31 - 1)]


class FreezeConfirmation(Command):
    confirmation: str = Field(min_length=30, max_length=2000, pattern=r"\S")
    unrecorded_operations_resolved: Literal[True]


class Decision(Command):
    reason: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class ReturnToCount(Decision):
    item_ids: list[PositiveBigint] | None = Field(default=None, min_length=1, max_length=10000)


class FoundItem(Command):
    product_id: Sku
    location_id: PositiveBigint


class CountQuantity(Command):
    actual_quantity: Quantity

    @field_validator("actual_quantity", mode="before")
    @classmethod
    def exact_quantity(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("Use an integer or a decimal string for actual_quantity")
        return value


class DocumentResponse(BaseModel):
    document_id: int
    title: str
    mode: Literal["full", "partial"]
    warehouse_location_id: int
    status: DocumentStatus
    version: int
    current_revision: int | None
    submitted_revision: int | None
    created_by: str
    created_at: datetime
    started_at: datetime | None
    closed_at: datetime | None
    updated_at: datetime
    locations: list[LocationSelection]
    product_ids: list[str]
    location_count: int
    item_count: int
    preliminary_only: bool = True
    apply_enabled: bool = False


class ItemResponse(BaseModel):
    item_id: int
    document_id: int
    product_id: str
    product_name: str | None
    location_id: int
    location_code: str
    initial_expected_quantity: Decimal
    actual_quantity: Decimal | None
    initial_discrepancy: Decimal | None
    discovered: bool
    needs_recount: bool
    latest_attempt_id: int | None
    count_status: Literal["not_counted", "in_progress", "counted", "recount_required"]
    conflicts: list[str]


class AttemptResponse(BaseModel):
    attempt_id: int
    document_id: int
    item_id: int
    attempt_number: int
    revision_no: int
    author: str
    status: Literal["in_progress", "completed", "cancelled"]
    started_at: datetime
    completed_at: datetime | None
    actual_quantity: Decimal | None
    evidence: dict[str, Any]
    conflicts: list[str]


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ReconciliationLine(BaseModel):
    item_id: int
    product_id: str
    location_id: int
    initial_expected_quantity: Decimal
    actual_quantity: Decimal | None
    initial_discrepancy: Decimal | None
    current_expected_quantity: Decimal
    movements_since_start: list[dict[str, Any]]
    kiz_events_since_start: list[dict[str, Any]]
    conflicts: list[str]
    ambiguous: bool
    requires_recount: bool
    safe_for_apply: Literal[False] = False


class ReconciliationResponse(BaseModel):
    document: DocumentResponse
    revision_no: int
    observed_at: datetime
    preliminary_only: Literal[True] = True
    lines: list[ReconciliationLine]


class PreviewResponse(BaseModel):
    document_id: int
    locations: list[dict[str, Any]]
    positive_item_count: int
    conflicts: list[str]
    preliminary_only: Literal[True] = True


class AttemptCommandResponse(BaseModel):
    document: DocumentResponse
    attempt: AttemptResponse


class ItemCommandResponse(BaseModel):
    document: DocumentResponse
    item: ItemResponse


class HistoryEvent(BaseModel):
    event_id: int
    document_id: int
    document_version: int
    action: str
    author: str
    occurred_at: datetime
    details: dict[str, Any]


class RevisionResponse(BaseModel):
    document_id: int
    revision_no: int
    kind: str
    observed_at: datetime
    author: str
    evidence: dict[str, Any]
    preliminary_only: Literal[True]


class ApplicationComponentResponse(BaseModel):
    component_id: int
    item_id: int
    component_number: int
    stock_evidence: dict[str, Any]
    adjustment_quantity: Decimal | None = None
    target_quantity: Decimal | None = None


class ApplicationPreparationResponse(BaseModel):
    preparation_id: int
    document_id: int
    submitted_revision: int
    observed_document_version: int
    created_at: datetime
    created_by: str
    source_type: Literal["inventory_document"]
    can_apply: bool = False
    freeze_id: int | None = None
    freeze_epoch: int | None = None
    blockers: list[str]
    lines: list[dict[str, Any]]
    components: list[ApplicationComponentResponse]


class ApplicationResultResponse(BaseModel):
    document: DocumentResponse
    applied: bool = False
    movements: list[dict[str, Any]] = Field(default_factory=list)
    preparation: ApplicationPreparationResponse | None
    application: dict[str, Any] | None = None


class FreezeResponse(BaseModel):
    freeze_id: int
    document_id: int
    state: Literal["freezing", "frozen", "ready_to_apply", "invalidated", "released"]
    epoch: int
    created_by: str
    created_at: datetime
    confirmed_by: str | None
    confirmed_at: datetime | None
    confirmation: str | None
    released_at: datetime | None


class FreezeCommandResponse(BaseModel):
    document: DocumentResponse
    freeze: FreezeResponse


class FreezeEventResponse(BaseModel):
    event_id: int
    action: str
    author: str
    occurred_at: datetime
    location_id: int | None
    product_id: str | None
    fbs_shipment_id: int | None
    movement_id: int | None = None
    movement_created_at: datetime | None = None
    details: dict[str, Any]


class FreezeStatusResponse(BaseModel):
    freeze: FreezeResponse | None
    events: Page[FreezeEventResponse]
    conflicting_operations: list[dict[str, Any]] = Field(default_factory=list)
    conflicts_truncated: bool = False
