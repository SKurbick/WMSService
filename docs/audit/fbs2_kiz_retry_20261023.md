# Повторное списание ожидавших КИЗов FBS 2.0 — 23.10.2026

Выполнено по разрешению пользователя через рабочий
`POST /api/fbs-shipments/items/{item_id}/retry` на хосте. Локальные функции
списания напрямую и SQL writers остатков не вызывались.

Перед запуском зафиксирована выборка: 553 уникальных СЗ/КИЗа, 254 items,
51 shipment, 32 товара. Все СЗ существовали и не были отгружены; КИЗы были
registered без holder/движений, товар совпадал с FBS binding и item.
Свободного остатка в PUSHKINO-ФБС достаточно для всей выборки с учётом identified.
Проверено, что каждый retry item содержит ровно согласованные СЗ; автор FBS 2.0,
source=standard. Другие авторы и неподтверждённые новые items не включались.

Рабочий endpoint сопоставлен с общим task-level pipeline очереди. Перед bulk
проверена одна исправленная позиция: item 406028, СЗ 6099438915. Она успешно
прошла KIZ-aware flow, после чего выполнены оставшиеся 253 вызова.

Время выполнения: 15:13:35–15:14:58 МСК. Финальная проверка: 15:15:37 МСК.

| Проверка | Результат |
|---|---:|
| СЗ written_off с effect_quantity=1, is_shipped=true | 553 |
| КИЗы shipped и bindings shipped | 553 |
| Успешные items, task_resolution_status=completed | 254 |
| Родительские shipments completed | 51 |
| Ship movements | 254 |
| Суммарное количество движений | 553 |
| Повторные списания СЗ | 0 |
| Несовпадения movement quantity и task effects | 0 |
| Ошибки связей registry/KIZ operation/item/event/link | 0 |
| Несовпадения фактических остатков с before минус write-off, 32 SKU | 0 |
| Оставшиеся незавершённые task-results заявки FBS 2.0, source=standard | 0 |

После каждого item проверялась рабочая БД, а не только HTTP 200: точный task/product,
KIZ identity/state, assembly mark, movement/registry ref, завершённая KIZ ship operation,
operation item, KIZ movement link и ровно один shipped event для движения.
Финальная сверка подтверждает точное уменьшение физических остатков по всем 32 SKU.

Исходные сообщения и авторы сохранены; новые shipments для retry не создавались.
Схема БД и код этой операцией не менялись, новая миграция не требовалась.
Результат относится к сохранённым FBS task results; это не утверждение о будущих
доставках или сообщениях, не дошедших до WMS.
