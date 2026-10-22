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

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Sku = Annotated[str, StringConstraints(min_length=1, max_length=50, pattern=r"^\S(?:.*\S)?$")]
Quantity = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
DocumentStatus = Literal["draft", "counting", "pending_approval", "closed_without_adjustment"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def jsonb_text(self):
        validate_jsonb_value(self.model_dump())
        return self


class LocationSelection(Input):
    location_id: int = Field(gt=0)
    include_descendants: bool = True


class DocumentScope(Input):
    title: str = Field(min_length=1, max_length=200, pattern=r"\S")
    mode: Literal["full", "partial"]
    warehouse_location_id: int = Field(gt=0)
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
    expected_version: int = Field(gt=0, description="Optimistic document version")
    user_name: Name


class UpdateDocument(DocumentScope, Command):
    pass


class Decision(Command):
    reason: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class ReturnToCount(Decision):
    item_ids: list[int] | None = Field(default=None, min_length=1, max_length=10000)

    @model_validator(mode="after")
    def positive_ids(self):
        if self.item_ids is not None and any(i <= 0 for i in self.item_ids):
            raise ValueError("item_ids must be positive")
        return self


class FoundItem(Command):
    product_id: Sku
    location_id: int = Field(gt=0)


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
    preliminary_only: Literal[True] = True
    apply_enabled: Literal[False] = False


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
    status: Literal["in_progress", "completed"]
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
