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


# === FBS ===


class AssemblyTasksAlreadyProcessedError(DomainException):
    """Не все сборочные задания удалось атомарно захватить для списания"""

    pass


class FbsShipmentItemsUpdateError(DomainException):
    """Не все FBS-позиции группы удалось атомарно отметить успешными"""

    pass


# === Products ===


class ProductNotFoundError(DomainException):
    """Товар не найден"""

    pass


# === Tasks ===


class TaskNotFoundError(DomainException):
    """Заявка не найдена"""

    pass


class TaskForbiddenError(DomainException):
    """Заявка не принадлежит данному пользователю"""

    pass


class TaskInvalidStatusError(DomainException):
    """Недопустимый переход статуса заявки"""

    pass


class TaskPermissionDeniedError(DomainException):
    """Недостаточно прав для выполнения операции с заявкой"""

    pass


class TaskItemNotFoundError(DomainException):
    """Позиция заявки не найдена"""

    pass


# === Notifications ===


class NotificationNotFoundError(DomainException):
    """Уведомление не найдено"""

    pass
