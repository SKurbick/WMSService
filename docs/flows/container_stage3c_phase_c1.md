# Container Stage 3C Phase C1 — KIZ inside containers

## Contract

Active KIZ has one direct holder: `location_id XOR container_id`. Contained KIZ keeps
`location_id=NULL`; effective location is read through `containers.location_id`.
`fill` and `extract` accept additive `kiz_codes` (default `[]`), while `move` and
`unpack-all` derive contained identities server-side. KIZ-aware items are batch-less.
Physical quantity remains exclusively `movements → inventory`; links contain no quantity.

## Lock order

Container operations use: idempotency row → container → locations/contents/inventory in
canonical scope order → KIZ ordered by `kiz_id`. Existing loose transfer/ship/assignment
keep inventory before KIZ. Terminal transitions lock the holder projection before KIZ.
All identity, physical, contents, links and saved-result writes share one transaction.

## Deployment

1. Stop/serialize container and KIZ writers.
2. Run `20260918_container_c1_kiz_preflight.sql`; it always rolls back.
3. Apply `20260918_add_container_c1_kiz.sql` manually.
4. Grant the runtime role only `EXECUTE` on
   `wms.transition_kiz_container_holder(bigint,bigint,varchar)` if it is not owner.
5. Deploy application code and run targeted C1 smoke on dedicated test SKU/containers.
6. Verify `GET /api/system/kiz-integrity` returns no new violations.

Production migration and write smoke are not performed by Codex.

## Rollback / operations

Application rollback after migration is safe only to a version that tolerates additive
columns. DDL rollback is not automated once contained KIZ exist. Before any manual schema
rollback, extract/unpack all active contained KIZ through supported APIs and prove
`container_id IS NULL` for all KIZ. Never rewrite holders, inventory, links or movements
with direct cleanup SQL.

## Out of scope

Direct container shipment, FBS/kit/re-sorting/receipt in container, direct assignment to
container, arbitrary container-to-container KIZ transfer, nested containers, batch-aware
KIZ, owner restrictions and container adjustment remain unsupported.

## Validation

Disposable PostgreSQL coverage includes mixed identified/unidentified fill, extract,
move and unpack-all; idempotent replay/conflict; holder-aware reads/history/integrity;
contained terminal transitions; and the required races: same KIZ into two containers,
fill versus transfer/ship, extract versus move/terminal transition, move versus
unpack-all, and two extracts for the same KIZ. A loser receives a controlled conflict
and every final state is checked by `wms.check_kiz_holder_integrity()`.

Run the focused checks with a disposable database only:

```bash
env PYTHONPATH=. KIZ_TEST_DSN=<disposable-dsn> .venv/bin/pytest -q \
  tests/test_container_c1_schema.py tests/test_container_c1_integration.py
```
