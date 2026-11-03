# Inventory Stage 2A — единый release candidate

Кандидат основан на fc0662a6e2bd2213123d291c434a5db6fdddefe3 и содержит исходный
inventory overlay от 8aa94e4 плюс незакоммиченные исправления Stage 2A/B.1/B.2.
Сохранены автоматическая регистрация КИЗ (5380629), частичный import и immutable
correction audit (07daa45), turnover/history (fc0662a). Новый commit не создавался.

## Интеграция

- Git 3-way overlay не дал конфликтов runtime-кода. Три documentation conflicts
  разрешены сохранением независимых блоков обеих сторон. Conftest сохраняет
  20261023_add_kiz_product_corrections.sql перед inventory migrations.
- Auto/partial import создаёт registered без holder и без physical movement.
  Freeze не запрещает такой import. Если изменился relevant КИЗ-контекст, старый
  prepared apply отклоняется; количество registered не вычитается из адреса.
- Turnover сохраняет receive/ship-only контракт. Adjust учитывается в balances и
  standalone operations history/summary, не становится искусственным receive/ship.
- Единственная согласованная правка upstream runtime-файла — descriptions двух
  полей KizImportGroupRejection, без изменения required/type/default/валидации.
- Адресные integration tests: tests/test_inventory_release_compatibility_postgres.py.

## Миграции и компоненты

1. Сверить фактическую KИЗ/registry/container/prefix/organization схему и ledger.
   Наличие source-файла не доказывает installation state.
2. Для обновлённого КИЗ-history нужна 20261023_add_kiz_product_corrections.sql.
   Если она уже установлена эквивалентно, не запускать повторно. Это schema migration.
3. Inventory: 20261022_add_inventory_documents.sql →
   20261029_add_inventory_application_preparations.sql →
   20261030_add_inventory_location_freeze.sql → 20261030_add_inventory_physical_apply.sql.
4. До physical migration — 20261102_inventory_projection_preflight.sql.
   Явный runtime roster и scoped privileges — по Stage 2A deployment runbook.
   После — read-only 20261102_inventory_runtime_privileges_preflight.sql.
5. scripts/maintenance/20261023_correct_confirmed_kiz_products.sql содержит прошлые
   адресные исправления production. НЕ является автоматическим шагом deployment.
   Ни один maintenance/data-fix script в database bundle не включается.

Обновлять одним исходным artifact: API, standard/external FBS consumers, task-level/
multi-source processor, retry worker и КИЗ import consumer. Существующие настройки
очередей/режимов/ролей требуют операторской проверки, не меняются автоматически.
Все inventory defaults остаются false: MODULE, FREEZE, APPLY; pilot limit — 20.

## Упаковка и идентификация

.dockerignore исключает .env/secrets, .git, audit/data/docs/tests/scripts и release
metadata из runtime image. SQL разворачивается отдельно по allowlist. Исторические
tracked audit reports сохраняются в source provenance и не попадают в runtime/DB bundles.
Новые посторонние wild188901 audit и fbs2_missing_kiz text не копируются.

docker-compose.yml получает runtime env через WMS_ENV_FILE (по умолчанию .env),
а WMS_IMAGE позволяет выбрать проверенный image digest. Production запускать только
с утверждённым env file и --no-build; в этом поручении compose up не выполняется.
Compose config проверяется на disposable значениях, реальные flags/секреты не меняются.

Dockerfile фиксирует cached Python 3.12-slim digest, constraints.release.txt — уже
протестированные версии, без обновления зависимостей. pip не обновляется неограниченно.
OS fonts устанавливаются исходным apt шагом; byte-identical rebuild всего image не
обещается. Для deployment сохранять digest реально собранного/проверенного image.

До ручного commit manifest имеет статус candidate_not_committed: base_commit — это
fc0662a, а не commit тестируемого Stage 2A. Точный content идентифицируется git tree OID,
SHA-256 файлов и deterministic source archive. Проверять SHA перед использованием.
После ручного commit проверить HEAD^{tree} против source_tree, записать настоящий
commit и registry image digest в новый immutable manifest; не менять старый manifest.
Автоматический commit/tag/push/deployment запрещён.

## Staging и отключение

Сначала read-only preflight фактической схемы, roles/default ACL/sequence prerequisites
и partitions. Defaults MODULE/FREEZE/APPLY=false. Миграции применять вручную только к
неприменённой схеме, с ON_ERROR_STOP и полным rollback при ошибке. Forward upgrade уже
установленной старой Stage 2A требует отдельной процедуры.

До FREEZE обновить все FBS writers: старый binary не умеет context и отклонит ship.
MODULE → FREEZE при APPLY=false, explicit dry run/release; затем отдельный допуск малого
обычного partial pilot и APPLY. ФБС-зона, неоднозначные batches/containers/КИЗ не расширяются.
Не останавливать production RabbitMQ. Учесть manager51, default ACL и отсутствие 2027
partitions по readiness-аудиту 02.11, проверить их актуальность перед deployment.

Отключение: APPLY=false, дождаться начатых SQL transactions → FREEZE=false → новой
версией явно release всех active fences → только затем MODULE=false/rollback binary.
Не удалять applied movements, registry/application links или историю.
