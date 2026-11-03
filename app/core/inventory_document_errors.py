"""Errors scoped exclusively to inventory documents."""

from app.core.exceptions import DomainException


class InventoryDocumentError(DomainException):
    def __init__(self, message, *, code="INVENTORY_CONFLICT", status_code=409, details=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details
