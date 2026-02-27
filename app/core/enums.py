"""Enum типы для WMS"""

from enum import Enum


class ZoneType(str, Enum):
    """Типы зон склада"""

    RECEIVING = "receiving"  # Приёмка
    STORAGE = "storage"  # Хранение
    PICKING = "picking"  # Комплектация
    PACKING = "packing"  # Упаковка
    SHIPPING = "shipping"  # Отгрузка
    QUARANTINE = "quarantine"  # Карантин
    WAREHOUSE = "warehouse" # Склад

class MovementType(str, Enum):
    """Типы перемещений"""

    RECEIVE = "receive"  # Приёмка товара
    SHIP = "ship"  # Отгрузка товара
    TRANSFER = "transfer"  # Перемещение между локациями
    ADJUST = "adjust"  # Корректировка остатков
    WRITE_OFF = "write_off"  # Списание
    UNPACK = "unpack"

class ContainerStatus(str, Enum):
    """Статусы контейнера"""

    EMPTY = "empty"  # Пустой
    SEALED = "sealed"  # Запечатан
    OPEN = "open"  # Вскрыт
    IN_TRANSIT = "in_transit"  # В пути
    BLOCKED = "blocked"  # Заблокирован


class ContainerType(str, Enum):
    """Типы контейнеров"""

    PALLET = "pallet"  # Паллета
    BOX = "box"  # Коробка
    CAGE = "cage"  # Клетка
    TROLLEY = "trolley"  # Тележка


class InventoryStatus(str, Enum):
    """Статусы инвентаря"""

    AVAILABLE = "available"  # Доступен
    RESERVED = "reserved"  # Зарезервирован
    QUARANTINE = "quarantine"  # На карантине
    DAMAGED = "damaged"  # Повреждён


class TaskType(str, Enum):
    """Типы заявок"""

    REPLENISHMENT = "replenishment"          # Пополнение
    TRANSFER = "transfer"                    # Перемещение
    PICKING = "picking"                      # Комплектация
    PUTAWAY = "putaway"                      # Размещение
    RECOUNT = "recount"                      # Пересчёт
    DISCREPANCY_APPROVAL = "discrepancy_approval"  # Подтверждение расхождения


class TaskStatus(str, Enum):
    """Статусы заявок"""

    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    COMPLETED_WITH_DISCREPANCY = "completed_with_discrepancy"
    PENDING_APPROVAL = "pending_approval"
    PENDING_RECOUNT = "pending_recount"
    WAITING_RECOUNT = "waiting_recount"
    CANCELLED = "cancelled"


class NotificationSeverity(str, Enum):
    """Важность уведомления"""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
