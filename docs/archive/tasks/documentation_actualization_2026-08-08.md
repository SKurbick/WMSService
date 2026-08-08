# Чек-лист актуализации документации WMSService

> Статус: основной runtime-аудит завершён, обновлено 2026-08-08.
>
> Файл не является документацией сервиса или источником истины. После завершения
> актуализации его можно удалить. Исходная развернутая версия временно сохранена в
> `/tmp/WMSService_DOCUMENTATION_ACTUALIZATION_TZ.initial.md`.

## Выполнено

- [x] Заполнен корневой `README.md`: назначение, стек, локальный запуск, RabbitMQ flags,
  Docker-ограничения, миграции, тесты и вход в документацию.
- [x] Создан `docs/README.md` с приоритетом источников истины, порядком чтения и
  классификацией каждого материала как `CURRENT`, `HISTORICAL`, `PROPOSAL`,
  `DB SNAPSHOT` или `UTILITY`.
- [x] Historical/proposal/snapshot материалы отделены от текущей документации на уровне
  центрального индекса.
- [x] `api_map.md` статически сверена с FastAPI routers по основным группам. В карте есть
  history, receipt, kit, re-sorting, item retry и HTTP FBS ingestion.
- [x] OpenAPI history contract проверен тестом:
  `env PYTHONPATH=. .venv/bin/pytest -q -p no:cacheprovider tests/test_history_openapi.py`
  — 5 tests passed.
- [x] Создан `scripts/migrations/README.md`: найденные файлы, хронологический порядок,
  ограничения повторного применения, ручной `http_api` constraint, отсутствие rollback
  и обязательные проверки целевой БД.
- [x] Зафиксировано, что `wms_schema.sql` — неполный DB snapshot: он содержит часть
  ранних изменений, но не re-sorting migration.

## Следующий простой этап

- [x] Добавить status banner непосредственно во все существующие
  historical/proposal/snapshot файлы: `api_gap_analysis.md`, `risk_map.md`,
  `receipt_writer_flow_analysis.md`, `product_movement_history_proposal.md`,
  `unified_history_and_receipt_revisions_architecture.md` и DB snapshot docs.
- [x] Проверено, что `saga_fbo_wms_analysis.md` и
  `kit_operations_orchestration_audit.md` отсутствуют в HEAD и Git history; ложные
  ссылки удалены из `docs/README.md`, временный open question закрыт.
- [x] В `current_state.md` заменить утверждение «receipt history не реализована» на
  уточнение: receipt headers не входят в unified operations list, но отдельные
  receipt-history endpoints реализованы.
- [x] В `api_gap_analysis.md` убрать реализованный
  `POST /api/fbs-shipments/items/{item_id}/retry` из активных gaps.
- [x] В `database_map.md` заменить «таблицы резервов ожидаются вне кода» на ссылку на
  `scripts/migrations.sql`, отдельно отметив, что применение в окружении не доказано.
- [x] В proposals добавить таблицу implemented/proposal для operations/receipt history.

## Следующий средний этап

- [x] Оставить `current_state.md` сопровождаемым snapshot текущих возможностей;
  датированную историю вести в `decisions.md`, отдельный changelog не создавать.
- [x] Выполнена полная машинная сверка всех 88 method/path операций `api_map.md`
  с OpenAPI; hidden/deprecated routes отсутствуют. Добавлен regression-тест
  `tests/test_api_map_openapi.py`.
- [x] Проверены внутренние Markdown-ссылки и все записи центрального каталога;
  добавлен regression-тест `tests/test_documentation_links.py`.
- [x] Унифицирован формат `decisions.md`: одинаковый уровень датированных записей,
  metadata статуса, связанных endpoints/миграций и superseded status; исходный
  контекст, решения и последствия сохранены.
- [x] Проведён triage `open_questions.md`: каждому вопросу назначены ID, статус,
  дата, владелец-роль и ссылка на основание/issue.
- [x] Проведён repository triage `known_issues.md`: каждый пункт подтверждён кодом,
  миграцией или DB snapshot и помечен, если ещё требуется runtime verification.

## Сложный этап, требующий runtime PostgreSQL или владельца БД

- [x] Подготовлены read-only `runtime_schema_audit.sql` и инструкция
  `runtime_db_audit.md`; выполнение и schema-only dump ожидаются от владельца БД.

- [x] Получить актуальный schema-only dump production БД `vector_db`.
- [x] Установить дату, production-окружение, БД и commit runtime-аудита; происхождение старого `wms_schema.sql` остаётся неизвестным.
- [x] Сравнить runtime schema с `wms_schema.sql` и всеми миграциями.
- [x] Подтвердить создание `ltree`, примененные constraints, triggers, functions,
  indexes, views и актуальные partitions `wms.movements`.
- [x] Подтвердить применение ручного расширения `chk_fbs_shipments_source` для
  `http_api`.
- [x] После DB-сверки актуализировать `database_map.md`, `database_functions.md`,
  `database_triggers.md`, `database_indexes_constraints.md`, `invariants.md`,
  `known_issues.md` и `risk_map.md`.
- [ ] Проверить на отдельной development/stage PostgreSQL транзакции и конкурентные сценарии остатков,
  movements, containers, FBS retry, kit и re-sorting; production `vector_db` для write-тестов запрещена.

## Техническое состояние инструментов

`apply_patch` по-прежнему не читает существующие файлы из-за
`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`. Этот дефект больше не
блокирует текущий этап: точечные изменения применены контролируемым механическим
скриптом с проверкой исходных фрагментов, после чего `git diff --check` прошёл успешно.

## Критерии завершения

- [x] Новый разработчик однозначно понимает порядок чтения.
- [x] У каждого материала виден статус непосредственно в файле и в индексе.
- [x] Реализованные endpoints нигде не перечислены как отсутствующие.
- [x] Proposals не воспринимаются как текущий контракт.
- [x] DB snapshot не воспринимается как гарантированная runtime schema.
- [x] Порядок миграций и ручные операции однозначны.
- [x] Open questions и known issues имеют актуальные статусы.
- [ ] Правила остатков и конкурентности подтверждены кодом и runtime PostgreSQL.
