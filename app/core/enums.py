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
    WAREHOUSE = "warehouse"  # Склад


class MovementType(str, Enum):
    """Типы перемещений"""

    RECEIVE = "receive"  # Приёмка товара
    SHIP = "ship"  # Отгрузка товара
    TRANSFER = "transfer"  # Перемещение между локациями
    ADJUST = "adjust"  # Корректировка остатков
    WRITE_OFF = "write_off"  # Списание
    UNPACK = "unpack"
    KIT_ASSEMBLY = "kit_assembly"  # Комплектация комплекта
    KIT_DISASSEMBLY = "kit_disassembly"  # Разукомплектация комплекта
    RE_SORTING = "re_sorting"  # Пересортица


class KitOperationType(str, Enum):
    """Типы операций с комплектами"""

    ASSEMBLY = "assembly"
    DISASSEMBLY = "disassembly"


class KitOperationStatus(str, Enum):
    """Статусы операций с комплектами"""

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class KitOperationItemRole(str, Enum):
    """Роль строки операции комплекта"""

    COMPONENT_CONSUMPTION = "component_consumption"
    KIT_RESULT = "kit_result"
    KIT_CONSUMPTION = "kit_consumption"
    COMPONENT_RESULT = "component_result"


class ReSortingOperationStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ReSortingOperationItemRole(str, Enum):
    SOURCE_OUTGOING = "source_outgoing"
    TARGET_INCOMING = "target_incoming"


class FbsShipmentSource(str, Enum):
    """Источник FBS-отгрузки"""

    STANDARD = "standard"
    EXTERNAL_DETECTED = "external_detected"


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

    REPLENISHMENT = "replenishment"  # Пополнение зоны (например, комплектации)
    TRANSFER = "transfer"  # Перемещение между зонами
    PICKING = "picking"  # Комплектация (сбор для отгрузки)
    PUTAWAY = "putaway"  # Размещение после приёмки
    RECOUNT = "recount"  # Пересчёт товара (служебная заявка)
    DISCREPANCY_APPROVAL = (
        "discrepancy_approval"  # Подтверждение расхождения (служебная заявка для админа)
    )


class TaskStatus(str, Enum):
    """Статусы заявок"""

    PENDING = "pending"  # Ожидает назначения на сотрудника
    ASSIGNED = "assigned"  # Назначена на сотрудника
    IN_PROGRESS = "in_progress"  # Выполняется
    COMPLETED = "completed"  # Завершена без расхождений
    COMPLETED_WITH_DISCREPANCY = (
        "completed_with_discrepancy"  # Завершена с расхождениями (админ принял)
    )
    PENDING_APPROVAL = "pending_approval"  # Ждёт подтверждения админа (есть расхождение)
    PENDING_RECOUNT = "pending_recount"  # Отправлена на пересчёт
    WAITING_RECOUNT = "waiting_recount"  # Ждёт результата пересчёта (для дочерней заявки)
    CANCELLED = "cancelled"  # Отменена


class NotificationSeverity(str, Enum):
    """Важность уведомления"""

    INFO = "info"  # Информационное (например, заявка назначена)
    WARNING = "warning"  # Предупреждение (расхождение 5-20%)
    CRITICAL = "critical"  # Критическое (расхождение >20% или системная ошибка)
