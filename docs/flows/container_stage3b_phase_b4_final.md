# Container Stage 3B B4 Final — rollout and runtime-role debt

## Scope

B4 закрывает public/runtime обходы container operations: generic movements становятся
loose-only, register — empty-only, legacy move/unpack удаляются. Supported container
physical writers: fill, extract, move, unpack-all.

## Manual database rollout

Codex не применяет migration к production.

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f scripts/migrations/20260917_container_b4_final_preflight.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f scripts/migrations/20260917_add_container_b4_final.sql
```

Preflight должен завершиться без exception. Migration:

- добавляет guard container-coded movements;
- удаляет legacy container location trigger/function;
- удаляет `wms.unpack_from_container`;
- заменяет `wms.register_container` на empty-only implementation;
- не создаёт business movements и не меняет physical quantities.

Migration нужно применить до запуска B4-кода: код уже не публикует legacy routes, а DB
после migration больше не поддерживает их функции.

## Smoke checks

1. Empty register возвращает 201 и `items_registered=0`.
2. Register с contents возвращает 400 `CONTAINER_CONTENTS_NOT_ALLOWED`.
3. Generic loose receive/transfer/adjust возвращают 201.
4. Любой generic batch с `container_code` возвращает 400
   `GENERIC_CONTAINER_MOVEMENT_NOT_ALLOWED`, movements/inventory не меняются.
5. Legacy move/unpack отсутствуют в OpenAPI и возвращают 404/405.
6. Fill/extract/move/unpack-all и replay/conflict regression проходят.
7. Recalculate на согласованном container graph проходит; при mismatch возвращает 409
   `CONTAINER_INVENTORY_INTEGRITY_ERROR` и полностью откатывается.
8. Audit-summary container counters равны ожидаемому состоянию.

## Runtime role infrastructure debt

Текущий `vector_admin` является superuser; B4 не меняет production credentials. Целевая
схема deployment:

- отдельная application LOGIN role без `SUPERUSER`, `CREATEDB`, `CREATEROLE`;
- отдельная owner/migration role;
- runtime grants: CONNECT, USAGE schema, нужные SELECT/DML/sequence и EXECUTE только
  supported functions;
- revoke `PUBLIC EXECUTE` у mutation functions после inventory consumers;
- credential cutover со stage acceptance и rollback plan.

Grants сами по себе не различат generic и controlled inserts в shared `wms.movements`;
B4 DB trigger остаётся обязательным дополнительным guard.
