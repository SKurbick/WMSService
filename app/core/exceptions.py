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


class InventoryHistoryValidationError(DomainException):
    """Некорректные параметры запроса истории остатков."""

    pass


class OperationsHistoryValidationError(DomainException):
    """Некорректные параметры единого списка операций."""

    pass


class OperationsHistoryEventIdError(DomainException):
    """Некорректный event ID истории операций."""

    pass


class OperationsHistoryNotFoundError(DomainException):
    """Операция по event ID не найдена."""

    pass


class ReceiptHistoryValidationError(DomainException):
    """Некорректный запрос истории поступления."""

    pass


class ReceiptHistoryNotFoundError(DomainException):
    """Документ поступления не найден."""

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


class InconsistentFbsShipmentError(DomainException):
    """СЗ отгружены, но не подтверждены success FBS item с movement_id."""

    pass


# === Products ===


class ProductNotFoundError(DomainException):
    """Товар не найден"""

    pass


# === Kit Operations ===


class KitOperationNotFoundError(DomainException):
    """Операция комплекта не найдена"""

    pass


class KitOperationValidationError(DomainException):
    """Некорректный запрос операции комплекта"""

    pass


class KitOperationConflictError(DomainException):
    """Конфликт состояния для операции комплекта"""

    pass


# === Re-sorting Operations ===


class ReSortingOperationNotFoundError(DomainException):
    pass


class ReSortingOperationValidationError(DomainException):
    pass


class ReSortingOperationConflictError(DomainException):
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


# === System ===


class RecalculateInventoryFromDateNotAllowedError(DomainException):
    """Частичный пересчет inventory по from_date временно запрещен"""

    pass


class NegativeCalculatedInventoryError(DomainException):
    """Пересчет inventory из movements дал отрицательный остаток"""

    pass


# === Notifications ===


class NotificationNotFoundError(DomainException):
    """Уведомление не найдено"""

    pass
