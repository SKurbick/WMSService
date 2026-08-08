# Runtime Concurrency Verification Plan

Статус: `CURRENT`; план подготовлен, write-сценарии не запускались.

## Gate

Production БД `vector_db` подтверждена владельцем и исключена из write-проверок. Выполнять только на отдельном development/stage окружении после явного подтверждения. Для каждого сценария нужны выделенные тестовые SKU, locations и containers, которые не используются приложением. До запуска зафиксировать baseline, использовать уникальный префикс тестовых данных и подготовить проверяемый cleanup. На production сценарии запрещены.

## Матрица

| Сценарий | Параллельное действие | Проверяемый результат |
|---|---|---|
| Movement/inventory | Два конкурентных списания одного physical stock key | Не возникает отрицательного остатка; ровно допустимое число transactions commit |
| Container unpack | Две полные/частичные распаковки одного content row | Нет double-unpack; итог container contents и inventory согласованы |
| Container relocation | Перемещение контейнера одновременно с изменением contents | Location контейнера, movements и container inventory согласованы |
| FBS retry | Два retry одного `fbs_shipment_items.item_id` | Не создаются два movement; item связан с единственным результатом |
| Kit operation | Две операции одного `kit_product_id + location_id` | Advisory lock сериализует flow; расходные rows блокируются |
| Re-sorting | Одинаковая canonical SKU pair и source location | Advisory и row locks сериализуют flow; net delta операции равен нулю |

## Протокол

1. Подтвердить окружение и владельца тестовых fixtures.
2. Создать fixtures отдельной setup-транзакцией и записать их IDs.
3. Запустить два независимых соединения с контролируемыми barriers/timeouts.
4. Проверить commit/rollback, movements, inventory и operation/item statuses третьим read-only соединением.
5. Сохранить обезличенный результат и удалить только fixtures по точным IDs.
6. При любом неоднозначном ownership cleanup остановить и передать удаление владельцу БД.

## Не входит

- Исправление legacy movement и `VALIDATE CONSTRAINT`.
- Нагрузочное тестирование и production rollout.
- Изменение business rules, lock policy или схемы БД.
