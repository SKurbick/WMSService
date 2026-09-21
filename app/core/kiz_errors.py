"""Stable PostgreSQL error codes shared by HTTP and background writers."""

import asyncpg

from app.core.exceptions import DomainException


class KizGuardError(asyncpg.PostgresError):
    sqlstate = "P7501"


class KizConflictError(DomainException):
    def __init__(self, message: str, *, diagnostics=None, error_code="KIZ_CONFLICT"):
        super().__init__(message)
        self.diagnostics = diagnostics
        self.error_code = error_code


class KizTransferConflictError(KizConflictError):
    """The normalized transfer cannot be applied to the locked physical/KIZ state."""


class KizShipConflictError(KizConflictError):
    """The normalized shipment cannot be applied to the locked physical/KIZ state."""


class KizNotFoundError(DomainException):
    pass


def write_conflict_code(exc):
    if isinstance(exc, KizConflictError):
        return exc.error_code
    if getattr(exc, "sqlstate", None) == "P7501":
        return "KIZ_CONFLICT"
    if getattr(exc, "sqlstate", None) in {"40001", "40P01"}:
        return "CONCURRENT_WRITE_CONFLICT"
    return None


def write_conflict_message(exc):
    if write_conflict_code(exc) == "CONCURRENT_WRITE_CONFLICT":
        return "Конкурентное изменение остатка. Повторите операцию целиком."
    return str(exc)
