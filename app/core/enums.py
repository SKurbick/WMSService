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
