# Пробное объединение KIZ и FBS

Дата: 2026-10-20. Режим: моделирование, без изменения исходного кода двух рабочих веток, без commit, без применения миграций и без подключения к БД.

## Что объединялось

- Основа: `feature/kiz-fbs-integration`, commit `9784cfd89dffed1108df58e853e981f67baf7964`.
- Вторая линия: `feature/kiz`, commit `02b0507e51edbd9c30562be88064f277ede84cfb`.
- Общий предок: `249c54aeb0047c60a7287fc2d41df8bc56bfc045`.
- Учтены текущие незакоммиченные изменения интеграционной ветки: Stage 1 FBS↔KIZ, organization, schema-first hotfix и связанные тесты/документы.
- Учтены незакоммиченные изменения русской документации в рабочем каталоге `feature/kiz`.

Сначала выполнен `git merge-tree --write-tree --name-only HEAD feature/kiz`: 17 конфликтующих файлов для закоммиченных версий. Команда создала только Git-объекты результата; branch refs, index и рабочие файлы не менялись.

Затем в `/tmp/wms-kiz-merge-preview` сформирована отдельная копия с трёхсторонним объединением текущих файлов обеих веток относительно общего предка. Секретные ignored-файлы и `.env` в копию не переносились. Скрипт построения: `/tmp/wms_kiz_merge_preview.py`. Машинный перечень результатов: `/tmp/wms-kiz-merge-preview/merge-preview.json`.

## Результат текстового объединения

С учётом незакоммиченных изменений рассмотрены 204 пути. Конфликты найдены в 19 файлах: 8 Python-файлах, `.gitignore` и 10 файлах документации/описания миграций.

Python-конфликты:

- `app/api/v1/dependencies.py`
- `app/api/v1/endpoints/fbs_shipments.py`
- `app/api/v1/endpoints/kiz.py`
- `app/api/v1/router.py`
- `app/handlers/write_off_fbs_handler.py`
- `app/main.py`
- `app/middleware/error_handler.py`
- `app/retry_worker.py`

Остальные: `.gitignore`, `docs/current/api_map.md`, `business_rules.md`, `current_state.md`, `domain_model.md`, `invariants.md`, `known_issues.md`, `open_questions.md`, `docs/database/map.md`, `docs/decisions/decisions.md`, `scripts/migrations/README.md`.

Выбор одной стороны целиком потеряет часть функционала. Например, `endpoints/kiz.py` — конфликт add/add: одна сторона содержит activation, другая — assignment, карточки, список и lifecycle. Нужно сохранить оба набора маршрутов.

## Подтверждённые несовместимости контрактов

Локальная проверка: `/tmp/wms_kiz_merge_contract_checks.py`, импорт моделей из пробной копии, без запуска приложения и без БД.

| Сценарий | Фактический результат |
|---|---|
| Карточка ручного active КИЗ | Принимается |
| Карточка registered receipt КИЗ | Ошибка валидации `lifecycle_status` и `origin_type` |
| Карточка receipt КИЗ после activation | Ошибка валидации `origin_type` |
| Событие registered | Ошибка валидации `event_type`, `to_status` |
| Событие activated | Ошибка валидации `event_type`, `from_status` |

Причина: `app/core/schemas/kiz.py` из ранней ветки допускает только active/error/deactivated/shipped, происхождение warehouse_assignment и ранний набор событий. В новой линии registered/receipt_import/activated уже являются реализованным бизнес-контрактом.

Для объединения нужно расширить read-схемы и описание lifecycle/events, проверить фильтры списка и историю. У registered КИЗ оба holder должны оставаться пустыми; registered нельзя начинать учитывать в physical identified.

## Два класса KizConflictError

После автоматического объединения остаются два независимых класса с одинаковым именем:

- `app.core.exceptions.KizConflictError` — используется activation и текущим movement service;
- `app.core.kiz_errors.KizConflictError` — используется реестром, explicit operations и общим классификатором `write_conflict_code`.

Локально подтверждено: это разные классы. `write_conflict_code` возвращает `KIZ_CONFLICT` для второго и `None` для первого.

При механическом разрешении imports/handlers можно потерять правильный HTTP-код, диагностику или обработку ошибок другой линии. Нужно согласовать типы/совместимость обработчиков, сохранив существующие публичные error_code. Это техническая несовместимость, а не новое бизнес-правило.

## FBS и повторные попытки

Обе линии меняют primary FBS handler, automatic retry и manual item retry.

- Ранняя ветка добавляет обработку `KizGuardError`/SQLSTATE `P7501`, конкурентных ошибок и запись причины конфликта.
- Stage 1 добавляет `FbsKizProcessingError`, признак retryable, canonical binding, per-СЗ movement_ref и mixed-source processing.
- Нужно сохранить оба набора: постоянные ошибки binding не должны превращаться в бесконечные retry, а active physical guard не должен пропадать или становиться 500.
- Единственное поле `fbs_shipment_items.movement_id` не представляет multi-source. Для него нужно сохранить Stage 1 per-СЗ task results и явное поведение при нескольких движениях.

Уже утверждённые правила сохраняются:

- Без КИЗ: источник — FBS zone.
- Registered КИЗ: источник — FBS zone, переход registered → shipped.
- Active loose КИЗ: источник — фактический holder, переход active → shipped.
- Active container КИЗ: controlled reject в FBS Stage 1.

## Контейнеры и миграции

Полный container lifecycle и базовые KIZ/movement-registry migrations находятся в `feature/kiz`; поздние receipt/activation/FBS migrations — в интеграционной линии. После объединения должны присутствовать обе последовательности.

В поздних migrations activation и FBS shipment проверено наличие ветвей авторизации смены container holder: автоматической потери container-протокола по одному факту появления новых функций не обнаружено. Это статическая проверка SQL, не доказательство runtime-совместимости.

Повторно выполнять уже применённые migrations нельзя: часть SQL использует обычные CREATE/ALTER без повторного применения. Перед deployment нужна read-only сверка фактической схемы и план только недостающих изменений. Изменять исторические migrations для уже работающей БД недостаточно; если потребуется новое изменение схемы, нужна отдельная согласующая migration.

## Проверки

В пробной копии выполнены тесты, которым не нужен запуск приложения с ещё не разрешёнными конфликтами:

```text
tests/test_kiz_transfer_schema.py
tests/test_kiz_ship_schema.py
tests/test_kiz_operation_fingerprint.py
37 passed
```

Отдельно воспроизведены четыре несовместимости read-моделей и различие классов ошибок, описанные выше.

Приложение из пробной копии не запускается до разрешения текстовых конфликтов. Полный набор integration/concurrency tests и общий сценарий receipt → activation → transfer/container → FBS → history не выполнялись. Production smoke не проводился.

## Нужны ли решения владельца

На этом этапе новых бизнес-вопросов не обнаружено. Направление объединения следует уже утверждённым правилам: сохранить весь уникальный функционал обеих веток, registered без резерва, active physical guard и ограничение FBS container shipment до Stage 2.

Требуется инженерное согласование маршрутов, read-контрактов, исключений, FBS/retry и миграционной последовательности. Подтвердить отсутствие других конфликтов можно только после разрешения этих участков и общих тестов.

## Следующий этап

1. Сохранить текущие незакоммиченные изменения обеих линий в воспроизводимом виде.
2. Разрешить конфликты с сохранением всех API и нового FBS task-level поведения.
3. Согласовать registered/receipt_import/activated и единый контракт ошибок.
4. Выполнить unit/API tests, затем PostgreSQL integration/concurrency tests в изолированной БД.
5. Сверить целевую схему и выполнить общий post-migration smoke только на testwild/testwild2.

МОДЕЛИРОВАНИЕ ЗАВЕРШЕНО: ДА.
ГОТОВО К ВЫКЛАДКЕ: НЕТ.
