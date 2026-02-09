"""Доменные исключения"""


class DomainException(Exception):
    """Базовое исключение для доменных ошибок"""

    pass


# === Locations ===


class LocationNotFoundError(DomainException):
    """Локация не найдена"""

    pass


class ParentLocationInactiveError(DomainException):
    """Родительская локация неактивна"""

    pass


class LocationNotActiveError(DomainException):
    """Локация неактивна"""

    pass


# === Containers ===


class ContainerNotFoundError(DomainException):
    """Контейнер не найден"""

    pass


class ContainerAlreadyExistsError(DomainException):
    """Контейнер с таким QR-кодом уже существует"""

    pass


class ContainerBlockedError(DomainException):
    """Контейнер заблокирован"""

    pass


class InsufficientContainerQuantityError(DomainException):
    """Недостаточное количество товара в контейнере"""

    pass


# === Inventory ===


class InsufficientInventoryError(DomainException):
    """Недостаточное количество товара на остатках"""

    pass


class InventoryNotFoundError(DomainException):
    """Остатки не найдены"""

    pass


# === Movements ===


class InvalidMovementError(DomainException):
    """Некорректное перемещение"""

    pass


class MovementNotFoundError(DomainException):
    """Перемещение не найдено"""

    pass


# === Products ===


class ProductNotFoundError(DomainException):
    """Товар не найден"""

    pass
