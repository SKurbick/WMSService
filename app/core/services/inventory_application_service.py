"""Atomic source-specific physical pilot, guarded by persistent location freezes."""
from decimal import Decimal

from app.core.inventory_document_errors import InventoryDocumentError
from app.core.services.inventory_document_service import (
    EvidenceIndex,
    dump,
    normalized,
    evidence_changed,
    expected_and_conflicts,
)
from app.core.services.inventory_freeze_service import (
    InventoryFreezeService,
    lock_active_freeze,
    freeze_token,
    freeze_error,
    public_freeze,
    lock_task_admission,
    validate_fixed_locations,
)
from app.infrastructure.database.queries import inventory_documents as doc_sql
from app.infrastructure.database.queries import inventory_applications as sql
from app.infrastructure.database.queries import inventory_freeze as freeze_sql
from app.infrastructure.database.repositories.inventory_document_repository import decode
from app.shared.config import settings


class InventoryApplicationService:
    def __init__(self, documents_service):
        self.documents = documents_service

    async def observation(self, conn, document, freeze):
        await lock_task_admission(conn)
        locations = await conn.fetch(doc_sql.FIXED_LOCATIONS, document["document_id"])
        ids = [row["location_id"] for row in locations]
        current = normalized(
            await self.documents.repository.capture(conn, ids, document["product_ids"])
        )
        index = EvidenceIndex(current)
        baseline = EvidenceIndex(decode(freeze["baseline"]) or current)
        blockers = set()
        if await conn.fetch(freeze_sql.CONFLICTING_TASKS, ids):
            blockers.add("warehouse_tasks_require_resolution")
        if document["mode"] != "partial" or len(ids) > settings.INVENTORY_PILOT_MAX_LOCATIONS:
            blockers.add("pilot_scope_unsupported")
        if freeze["state"] not in ("frozen", "ready_to_apply") or not freeze["confirmation"]:
            blockers.add("freeze_not_confirmed_or_invalidated")
        if any(row["location_code"] == settings.FBS_LOCATION_CODE for row in locations):
            blockers.add("configured_fbs_location_physical_boundary_unverified")
        if any(not row["was_active"] for row in locations):
            blockers.add("inactive_location")
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM wms.inventory WHERE location_id=ANY($1::bigint[]) AND status='available' AND container_code IS NOT NULL)",
            ids,
        ):
            blockers.add("pilot_container_or_empty_container_key_unsupported")
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM wms.kiz WHERE location_id=ANY($1::bigint[]) AND lifecycle_status='active')",
            ids,
        ):
            blockers.add("pilot_active_kiz_location")
        selected = await validate_fixed_locations(self.documents, conn, document, locations)
        if any(not row["was_active"] for row in selected):
            blockers.add("inactive_location")
        if await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM wms.locations l
          JOIN wms.locations f ON f.location_code=$2
          WHERE l.location_id=ANY($1::bigint[]) AND l.path OPERATOR(wms.<@) f.path)""",
            ids,
            settings.FBS_LOCATION_CODE,
        ):
            blockers.add("configured_fbs_location_physical_boundary_unverified")
        if {r["location_id"] for r in selected} != set(ids):
            blockers.add("location_hierarchy_changed")
        raw_items = await conn.fetch(doc_sql.ALL_ITEMS, document["document_id"])
        counted_pairs = {(r["product_id"], r["location_id"]) for r in raw_items}
        for pair in {
            (r["product_id"], r["location_id"]) for r in current["stocks"]
        } - counted_pairs:
            if expected_and_conflicts(index.pair(*pair))[0] > 0:
                blockers.add("unlisted_positive_stock_requires_count")
        lines = []
        components = []
        for item in raw_items:
            now = index.pair(item["product_id"], item["location_id"])
            before = baseline.pair(item["product_id"], item["location_id"])
            expected, conflicts = expected_and_conflicts(now)
            attempt = decode(item["attempt_evidence"]) or {}
            if item["attempt_status"] != "completed" or item["needs_recount"]:
                conflicts.append("final_count_incomplete")
            if attempt.get("freeze") != freeze_token(freeze):
                conflicts.append("final_count_not_in_current_freeze_epoch")
            if (
                "start" not in attempt
                or "finish" not in attempt
                or (
                    evidence_changed(attempt.get("start", now), now)
                    or evidence_changed(attempt.get("finish", now), now)
                )
            ):
                conflicts.append("state_changed_during_or_after_final_count")
            if evidence_changed(before, now):
                conflicts.append("state_changed_since_freeze_confirmation")
            actual = item["actual_quantity"]
            if actual is not None and actual > Decimal("99999999.99"):
                conflicts.append("physical_numeric_10_2_capacity_exceeded")
            delta = None if actual is None else actual - expected
            stocks = now["stocks"]
            for number, stock in enumerate(
                stocks
                or [
                    dict(
                        inventory_id=None,
                        physical_quantity="0.00",
                        batch_number=None,
                        container_code=None,
                        status="available",
                    )
                ],
                1,
            ):
                components.append(
                    dict(
                        item_id=item["item_id"],
                        number=number,
                        stock=stock,
                        delta=delta if len(stocks) <= 1 and not conflicts else None,
                        target=actual if len(stocks) <= 1 and not conflicts else None,
                    )
                )
            blockers.update(conflicts)
            lines.append(
                dict(
                    item_id=item["item_id"],
                    product_id=item["product_id"],
                    location_id=item["location_id"],
                    initial_expected_quantity=item["initial_expected_quantity"],
                    frozen_expected_quantity=expected,
                    actual_quantity=actual,
                    adjustment_quantity=delta,
                    conflicts=sorted(set(conflicts)),
                    physical_boundary_basis="operator_attestation_and_database_freeze",
                )
            )
        return current, lines, components, sorted(blockers)

    async def prepare_locked(self, conn, document, request, freeze):
        saved = await conn.fetchrow(
            sql.GET_PREPARATION, document["document_id"], request.submitted_revision
        )
        if saved is None:
            current, lines, components, blockers = await self.observation(conn, document, freeze)
            saved = await conn.fetchrow(
                sql.INSERT_PILOT,
                document["document_id"],
                request.submitted_revision,
                document["version"],
                request.user_name,
                dump(blockers),
                dump(current),
                dump(lines),
                not blockers,
                freeze["freeze_id"],
                freeze["epoch"],
            )
            for component in components:
                await conn.execute(
                    sql.INSERT_PILOT_COMPONENT,
                    saved["preparation_id"],
                    document["document_id"],
                    component["item_id"],
                    component["number"],
                    dump(component["stock"]),
                    component["delta"],
                    component["target"],
                )
            await conn.execute(doc_sql.BUMP_VERSION, document["document_id"])
        return await self.documents.preparation_response(conn, saved), document["document_id"]

    async def verified_preparation(self, conn, document, request, freeze):
        self.documents.require_submitted_revision(document, request)
        row = await conn.fetchrow(
            sql.GET_PREPARATION, document["document_id"], request.submitted_revision
        )
        if (
            row is None
            or not row["can_apply"]
            or row["freeze_id"] != freeze["freeze_id"]
            or row["freeze_epoch"] != freeze["epoch"]
        ):
            freeze_error(
                "An eligible preparation for this freeze/revision is required",
                "INVENTORY_APPLICATION_NOT_READY",
            )
        current, lines, _, blockers = await self.observation(conn, document, freeze)
        saved_index, current_index = EvidenceIndex(decode(row["evidence"])), EvidenceIndex(current)
        if any(
            evidence_changed(
                saved_index.pair(line["product_id"], line["location_id"]),
                current_index.pair(line["product_id"], line["location_id"]),
            )
            for line in lines
        ):
            blockers.append("relevant_evidence_changed_since_preparation")
        if dump(lines) != dump(decode(row["lines"])):
            blockers.append("application_lines_changed_since_preparation")
        if blockers:
            raise InventoryDocumentError(
                "Preparation is no longer current; restore window and recount",
                code="INVENTORY_APPLICATION_STALE",
                details={"blockers": blockers},
            )
        return row

    async def ready(self, document_id, request):
        if not settings.INVENTORY_FREEZE_ENABLED:
            freeze_error("Inventory freeze is disabled", "INVENTORY_FREEZE_DISABLED")

        async def run(conn, document):
            self.documents.require_submitted_revision(document, request)
            freeze = await lock_active_freeze(conn, document_id)
            if freeze is None or freeze["state"] != "frozen":
                freeze_error("Confirmed frozen window required")
            await self.verified_preparation(conn, document, request, freeze)
            await conn.execute(
                "UPDATE wms.inventory_freezes SET state='ready_to_apply' WHERE freeze_id=$1",
                freeze["freeze_id"],
            )
            await InventoryFreezeService(self.documents).event(
                conn,
                freeze,
                "ready_to_apply",
                request.user_name,
                {"submitted_revision": request.submitted_revision},
            )
            await conn.execute(doc_sql.BUMP_VERSION, document_id)
            return (
                dict(
                    document=await self.documents.require_document(conn, document_id),
                    freeze=public_freeze(await conn.fetchrow(freeze_sql.ACTIVE, document_id)),
                ),
                document_id,
            )

        return await self.documents.command(
            "ready_to_apply", request, run, document_id, isolation="read_committed"
        )

    async def result_locked(self, conn, document):
        row = await conn.fetchrow(sql.GET_APPLICATION, document["document_id"])
        prep = await conn.fetchrow(sql.LATEST_PREPARATION, document["document_id"])
        application = dict(row) if row else None
        if application:
            application.pop("transaction_id")
        return normalized(
            dict(
                document=document,
                application=application,
                applied=bool(row and row["status"] == "applied"),
                movements=[dict(r) for r in await conn.fetch(sql.MOVEMENTS, row["application_id"])]
                if row
                else [],
                preparation=await self.documents.preparation_response(conn, prep) if prep else None,
            )
        )

    async def apply(self, document_id, request):
        if not (
            settings.INVENTORY_MODULE_ENABLED
            and settings.INVENTORY_APPLY_ENABLED
            and settings.INVENTORY_FREEZE_ENABLED
        ):
            raise InventoryDocumentError(
                "Inventory physical apply is disabled",
                status_code=503,
                code="INVENTORY_APPLY_DISABLED",
            )

        async def run(conn, document):
            # Different keys may retrieve the committed result only with the same
            # submitted revision. Same UUID replay is handled before version checks.
            if document["status"] == "applied":
                if request.submitted_revision != document["submitted_revision"]:
                    freeze_error(
                        "Applied revision differs", "INVENTORY_SUBMITTED_REVISION_CONFLICT"
                    )
                return await self.result_locked(conn, document), document_id
            self.documents.require_submitted_revision(document, request)
            # Match maintenance's ledger-before-projection order, without blocking
            # other ledger inserts. NOWAIT: do not wait while holding document/fences.
            await conn.execute("LOCK TABLE wms.movements IN ROW EXCLUSIVE MODE NOWAIT")
            freeze = await lock_active_freeze(conn, document_id)
            if freeze is None or freeze["state"] != "ready_to_apply":
                freeze_error("Ready freeze required", "INVENTORY_APPLICATION_NOT_READY")
            products = sorted(
                {r["product_id"] for r in await conn.fetch(doc_sql.ALL_ITEMS, document_id)}
            )
            await conn.fetch(
                "SELECT id FROM public.products WHERE id=ANY($1::varchar[]) ORDER BY id FOR UPDATE NOWAIT",
                products,
            )
            locations = [
                r["location_id"] for r in await conn.fetch(doc_sql.FIXED_LOCATIONS, document_id)
            ]
            await conn.fetch(
                "SELECT location_id FROM wms.locations WHERE location_id=ANY($1::bigint[]) ORDER BY location_id FOR KEY SHARE NOWAIT",
                locations,
            )
            await conn.fetch(
                "SELECT inventory_id FROM wms.inventory WHERE location_id=ANY($1::bigint[]) ORDER BY product_id,location_id,inventory_id FOR UPDATE NOWAIT",
                locations,
            )
            prep = await self.verified_preparation(conn, document, request, freeze)
            aid = await conn.fetchval(
                sql.CREATE_APPLICATION,
                document_id,
                prep["preparation_id"],
                request.submitted_revision,
                freeze["freeze_id"],
                freeze["epoch"],
                request.user_name,
            )
            await conn.execute(
                "SELECT set_config('wms.inventory_application_id',$1,true)", str(aid)
            )
            item_map = {r["item_id"]: r for r in await conn.fetch(doc_sql.ALL_ITEMS, document_id)}
            for component in await conn.fetch(sql.PILOT_COMPONENTS, prep["preparation_id"]):
                delta = component["adjustment_quantity"]
                if delta is None:
                    freeze_error(
                        "Component allocation is undefined", "INVENTORY_APPLICATION_NOT_READY"
                    )
                if delta == 0:
                    continue
                item = item_map[component["item_id"]]
                movement = await conn.fetchrow(
                    sql.INSERT_ADJUSTMENT,
                    item["product_id"],
                    item["location_id"] if delta < 0 else None,
                    item["location_id"] if delta > 0 else None,
                    abs(delta),
                    request.user_name,
                    document_id,
                    component["component_id"],
                )
                ref = await conn.fetchval(
                    sql.LINK,
                    aid,
                    component["component_id"],
                    movement["movement_id"],
                    movement["created_at"],
                )
                if ref is None:
                    freeze_error(
                        "Movement registry entry missing", "INVENTORY_APPLICATION_INCOMPLETE"
                    )
            await conn.execute(
                "UPDATE wms.inventory_applications SET status='applied',applied_at=clock_timestamp() WHERE application_id=$1",
                aid,
            )
            await conn.execute(
                "UPDATE wms.inventory_documents SET status='applied',closed_at=clock_timestamp(),version=version+1,updated_at=clock_timestamp() WHERE document_id=$1",
                document_id,
            )
            await conn.execute(freeze_sql.RELEASE_FENCES, freeze["freeze_id"])
            await conn.execute(freeze_sql.RELEASE, freeze["freeze_id"])
            await conn.execute(doc_sql.RELEASE_CLAIMS, document_id)
            await InventoryFreezeService(self.documents).event(
                conn,
                freeze,
                "released_after_apply",
                request.user_name,
                {
                    "application_id": aid,
                    "preparation_id": prep["preparation_id"],
                    "approved_revision": request.submitted_revision,
                },
            )
            return (
                await self.result_locked(
                    conn, await self.documents.require_document(conn, document_id)
                ),
                document_id,
            )

        return await self.documents.command(
            "apply", request, run, document_id, isolation="read_committed"
        )
