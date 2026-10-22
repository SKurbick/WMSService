"""Inventory documents/counting. Intentionally has no movement/apply dependency."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal

import asyncpg

from app.core.inventory_document_errors import InventoryDocumentError
from app.infrastructure.database.queries import inventory_documents as q
from app.infrastructure.database.repositories.inventory_document_repository import decode
from app.shared.config import settings


def json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def dump(value):
    return json.dumps(value, default=json_default, sort_keys=True, ensure_ascii=False)


def normalized(value):
    return json.loads(dump(value))


def pair_evidence(evidence, product_id, location_id):
    """Project document evidence onto one count unit, never infer physical order."""
    return {
        "stocks": [
            r
            for r in evidence["stocks"]
            if r["product_id"] == product_id and r["location_id"] == location_id
        ],
        "kiz": [
            r
            for r in evidence["kiz"]
            if r["product_id"] == product_id and r["location_id"] in (None, location_id)
        ],
        "movements": [
            r
            for r in evidence["movements"]
            if r["product_id"] == product_id
            and location_id in (r["from_location_id"], r["to_location_id"])
        ],
        "kiz_events": [
            r
            for r in evidence["kiz_events"]
            if r["product_id"] == product_id and r["location_id"] in (None, location_id)
        ],
    }


class EvidenceIndex:
    """Group a warehouse snapshot once, rather than scan its ledger per item."""

    def __init__(self, evidence):
        self.groups = {key: {} for key in ("stocks", "kiz", "movements", "kiz_events")}
        for name, groups in self.groups.items():
            for row in evidence[name]:
                locations = (
                    {row["from_location_id"], row["to_location_id"]} - {None}
                    if name == "movements"
                    else {row["location_id"]}
                )
                for location in locations:
                    groups.setdefault((row["product_id"], location), []).append(row)

    def pair(self, product_id, location_id):
        result = {
            key: list(groups.get((product_id, location_id), []))
            for key, groups in self.groups.items()
        }
        for key, identity in (("kiz", "kiz_id"), ("kiz_events", "kiz_event_id")):
            result[key].extend(self.groups[key].get((product_id, None), []))
            result[key].sort(key=lambda row: row[identity])
        return result


def movement_identity(row):
    return row["movement_id"], str(row["created_at"])


def new_evidence_rows(before, after, key, identity):
    known = {identity(r) for r in before[key]}
    return [r for r in after[key] if identity(r) not in known]


def evidence_changed(before, after):
    # Canonical JSON normalizes Decimal/datetime returned by asyncpg vs stored JSON.
    return dump({k: before[k] for k in ("stocks", "kiz", "movements", "kiz_events")}) != dump(
        {k: after[k] for k in ("stocks", "kiz", "movements", "kiz_events")}
    )


def expected_and_conflicts(pair):
    stocks = pair["stocks"]
    physical = sum((Decimal(r["physical_quantity"]) for r in stocks), Decimal(0))
    active = sum(r["lifecycle_status"] == "active" for r in pair["kiz"])
    null_batch = sum(
        (Decimal(r["physical_quantity"]) for r in stocks if r["batch_number"] is None), Decimal(0)
    )
    conflicts = []
    if len(stocks) > 1:
        conflicts.append("multi_batch_allocation_undefined")
    if any(r["batch_number"] is not None for r in stocks):
        conflicts.append("batch_stock")
    if any(r["batch_number"] == "" for r in stocks):
        conflicts.append("empty_batch")
    if len({r["batch_number"] or "" for r in stocks}) != len(stocks):
        conflicts.append("null_empty_collision")
    if active:
        conflicts.append("active_kiz_physical_separation_required")
    if active > null_batch:
        conflicts.append("active_kiz_exceeds_physical")
    if any(r["lifecycle_status"] == "registered" for r in pair["kiz"]):
        conflicts.append("registered_kiz_without_holder")
    if any(not r["location_active"] for r in stocks):
        conflicts.append("inactive_location")
    if any(r.get("location_code") == settings.FBS_LOCATION_CODE for r in stocks):
        conflicts.append("configured_fbs_location_physical_boundary_unverified")
    return max(Decimal(0), physical - active), sorted(set(conflicts))


class InventoryDocumentService:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def require_status(document, *statuses):
        if document["status"] not in statuses:
            raise InventoryDocumentError(
                "Command is not allowed in this document state", code="INVENTORY_STATE_CONFLICT"
            )

    async def require_document(self, conn, document_id):
        document = await self.repository.document(conn, document_id)
        if document is None:
            raise InventoryDocumentError(
                "Inventory document not found", status_code=404, code="INVENTORY_DOCUMENT_NOT_FOUND"
            )
        return document

    async def require_item(self, conn, document_id, item_id):
        item = await conn.fetchrow(q.GET_ITEM, document_id, item_id)
        if item is None:
            raise InventoryDocumentError(
                "Inventory item not found", status_code=404, code="INVENTORY_ITEM_NOT_FOUND"
            )
        return dict(item)

    async def command(self, action, request, handler, document_id=None, extra=None):
        intent = dict(
            action=action,
            document_id=document_id,
            extra=extra,
            request=request.model_dump(mode="json"),
        )
        fingerprint = hashlib.sha256(dump(intent).encode()).hexdigest()
        # Only module rows/advisory keys are locked; no physical inventory/KIZ locks.
        for retry in range(3):
            try:
                async with self.repository.pool.acquire() as conn:
                    async with conn.transaction(isolation="repeatable_read"):
                        await conn.execute("SET LOCAL lock_timeout = '5s'")
                        await conn.execute("SET LOCAL statement_timeout = '15s'")
                        await conn.execute(q.LOCK_COMMAND, str(request.idempotency_key))
                        saved = await conn.fetchrow(q.GET_COMMAND, request.idempotency_key)
                        if saved:
                            if saved["request_hash"] != fingerprint:
                                raise InventoryDocumentError(
                                    "Idempotency key belongs to another command",
                                    code="INVENTORY_IDEMPOTENCY_CONFLICT",
                                )
                            return decode(saved["result"])
                        document = None
                        if document_id is not None:
                            locked = await conn.fetchrow(q.LOCK_DOCUMENT, document_id)
                            if locked is None:
                                await self.require_document(conn, document_id)
                            document = await self.require_document(conn, document_id)
                            if document["version"] != request.expected_version:
                                raise InventoryDocumentError(
                                    "Document version changed",
                                    code="INVENTORY_VERSION_CONFLICT",
                                    details={"current_version": document["version"]},
                                )
                        result, affected_id = await handler(conn, document)
                        result = normalized(result)
                        current = await self.require_document(conn, affected_id)
                        audit = dict(intent)
                        audit["transition"] = {
                            "from_status": document["status"] if document else None,
                            "to_status": current["status"],
                            "from_version": document["version"] if document else None,
                            "to_version": current["version"],
                            "revision_no": current["current_revision"],
                        }
                        await conn.execute(
                            q.INSERT_HISTORY,
                            affected_id,
                            current["version"],
                            action,
                            request.user_name,
                            dump(audit),
                        )
                        await conn.execute(
                            q.SAVE_COMMAND,
                            request.idempotency_key,
                            affected_id,
                            action,
                            fingerprint,
                            dump(result),
                        )
                        return result
            except (asyncpg.SerializationError, asyncpg.DeadlockDetectedError):
                if retry == 2:
                    raise InventoryDocumentError(
                        "Concurrent command; retry with the same key",
                        code="INVENTORY_CONCURRENT_COMMAND",
                    ) from None
            except asyncpg.UniqueViolationError as error:
                if error.constraint_name == "inventory_document_commands_pkey" and retry < 2:
                    # Concurrent create may wait after its RR snapshot was established.
                    # Retry with a fresh snapshot to return the saved command result.
                    continue
                code = (
                    "INVENTORY_SCOPE_OVERLAP"
                    if error.constraint_name == "inventory_location_claims_pkey"
                    else "INVENTORY_DUPLICATE"
                )
                raise InventoryDocumentError(
                    "Conflicting location claim, item or count attempt", code=code
                ) from None
            except asyncpg.LockNotAvailableError:
                raise InventoryDocumentError(
                    "Document is busy; retry with the same key", code="INVENTORY_CONCURRENT_COMMAND"
                ) from None

    async def resolve_scope(self, conn, spec):
        root_id = spec["warehouse_location_id"]
        selections = spec["locations"]
        ids = [root_id] + [r["location_id"] for r in selections]
        locations = {r["location_id"]: r for r in await conn.fetch(q.GET_LOCATIONS, ids)}
        root = locations.get(root_id)
        if root is None or root["parent_location_id"] is not None:
            raise InventoryDocumentError(
                "Warehouse must be an existing root location",
                code="INVENTORY_INVALID_WAREHOUSE",
                status_code=422,
            )
        if not root["is_active"]:
            raise InventoryDocumentError(
                "Warehouse root is inactive", code="INVENTORY_INACTIVE_WAREHOUSE"
            )
        if any(r["location_id"] not in locations for r in selections):
            raise InventoryDocumentError(
                "Selected location not found", status_code=404, code="INVENTORY_LOCATION_NOT_FOUND"
            )
        products = {r["id"] for r in await conn.fetch(q.GET_PRODUCTS, spec["product_ids"])}
        if products != set(spec["product_ids"]):
            raise InventoryDocumentError(
                "Selected SKU not found", status_code=404, code="INVENTORY_PRODUCT_NOT_FOUND"
            )
        parent_ids = {r["location_id"] for r in await conn.fetch(q.PARENT_TREE, root_id)}
        path_rows = await conn.fetch(q.EXPAND_SCOPE, root_id, [], [])
        if parent_ids != {r["location_id"] for r in path_rows}:
            raise InventoryDocumentError(
                "Warehouse parent/path hierarchy is inconsistent",
                code="INVENTORY_HIERARCHY_CONFLICT",
            )
        direct = [r["location_id"] for r in selections]
        subtree = [r["location_id"] for r in selections if r["include_descendants"]]
        rows = [dict(r) for r in await conn.fetch(q.EXPAND_SCOPE, root_id, direct, subtree)]
        if not set(direct).issubset({r["location_id"] for r in rows}):
            raise InventoryDocumentError(
                "Selected location is outside the warehouse",
                code="INVENTORY_SCOPE_OUTSIDE_WAREHOUSE",
            )
        return rows

    async def create(self, request):
        async def run(conn, _):
            await self.resolve_scope(conn, request.model_dump())
            row = await conn.fetchrow(
                q.CREATE_DOCUMENT,
                request.title,
                request.mode,
                request.warehouse_location_id,
                request.user_name,
            )
            document_id = row["document_id"]
            await conn.execute(
                q.SAVE_FILTERS,
                document_id,
                dump([r.model_dump() for r in request.locations]),
                request.product_ids,
            )
            return await self.require_document(conn, document_id), document_id

        return await self.command("create", request, run)

    async def update(self, document_id, request):
        async def run(conn, document):
            self.require_status(document, "draft")
            await self.resolve_scope(conn, request.model_dump())
            # Filters must be updated before any lifecycle transition (draft only).
            await conn.execute(
                q.SAVE_FILTERS,
                document_id,
                dump([r.model_dump() for r in request.locations]),
                request.product_ids,
            )
            await conn.execute(
                q.UPDATE_DRAFT,
                document_id,
                request.title,
                request.mode,
                request.warehouse_location_id,
            )
            return await self.require_document(conn, document_id), document_id

        return await self.command("update", request, run, document_id)

    async def preview(self, document_id):
        async with self.repository.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                document = await self.require_document(conn, document_id)
                self.require_status(document, "draft")
                scope = await self.resolve_scope(conn, document)
                evidence = await self.repository.capture(
                    conn, [r["location_id"] for r in scope], document["product_ids"]
                )
                pairs = {(r["product_id"], r["location_id"]) for r in evidence["stocks"]}
                index = EvidenceIndex(evidence)
                positive = sum(expected_and_conflicts(index.pair(*pair))[0] > 0 for pair in pairs)
                return dict(
                    document_id=document_id,
                    locations=scope,
                    positive_item_count=positive,
                    conflicts=["inactive_locations_included"]
                    if any(not r["was_active"] for r in scope)
                    else [],
                )

    async def start(self, document_id, request):
        async def run(conn, document):
            self.require_status(document, "draft")
            scope = await self.resolve_scope(conn, document)
            await conn.executemany(
                q.INSERT_LOCATION,
                [
                    (document_id, r["location_id"], r["location_code"], r["path"], r["was_active"])
                    for r in scope
                ],
            )
            # Sorted IDs + unique PK are authoritative for concurrent overlapping starts.
            await conn.executemany(q.INSERT_CLAIM, [(r["location_id"], document_id) for r in scope])
            evidence = await self.repository.capture(
                conn, [r["location_id"] for r in scope], document["product_ids"]
            )
            for r in evidence["stocks"]:
                await conn.execute(
                    q.INSERT_COMPONENT,
                    document_id,
                    r["inventory_id"],
                    r["product_id"],
                    r["location_id"],
                    r["status"],
                    r["batch_number"],
                    r["container_code"],
                    r["physical_quantity"],
                    r["active_kiz_quantity"],
                    r["physical_quantity"] - r["active_kiz_quantity"],
                    r["source_updated_at"],
                )
            await conn.executemany(
                q.INSERT_KIZ_SNAPSHOT,
                [
                    (
                        document_id,
                        r["kiz_id"],
                        r["product_id"],
                        r["location_id"],
                        r["container_id"],
                        r["lifecycle_status"],
                    )
                    for r in evidence["kiz"]
                ],
            )
            pairs = sorted({(r["product_id"], r["location_id"]) for r in evidence["stocks"]})
            index = EvidenceIndex(evidence)
            for pair in pairs:
                expected, conflicts = expected_and_conflicts(index.pair(*pair))
                if expected > 0:
                    await conn.execute(
                        q.INSERT_ITEM, document_id, *pair, expected, False, dump(conflicts)
                    )
            await conn.execute(
                q.INSERT_REVISION,
                document_id,
                1,
                "start",
                evidence["observed_at"],
                request.user_name,
                dump(evidence),
            )
            await conn.execute(q.START_DOCUMENT, document_id, evidence["observed_at"])
            return await self.require_document(conn, document_id), document_id

        return await self.command("start", request, run, document_id)

    @staticmethod
    def item_response(item):
        actual = item["actual_quantity"]
        status = (
            "recount_required"
            if item["needs_recount"]
            else "not_counted"
            if item["latest_attempt_id"] is None
            else "counted"
            if item["attempt_status"] == "completed"
            else "in_progress"
        )
        result = {
            key: item[key]
            for key in (
                "item_id",
                "document_id",
                "product_id",
                "product_name",
                "location_id",
                "location_code",
                "initial_expected_quantity",
                "discovered",
                "needs_recount",
                "latest_attempt_id",
            )
        }
        result.update(
            actual_quantity=actual,
            count_status=status,
            initial_discrepancy=None
            if actual is None
            else actual - item["initial_expected_quantity"],
            conflicts=sorted(
                set(decode(item["initial_conflicts"]) + (decode(item["attempt_conflicts"]) or []))
            ),
        )
        return result

    @staticmethod
    def attempt_response(attempt):
        result = dict(attempt)
        for key in ("evidence", "conflicts"):
            result[key] = decode(result[key])
        return result

    async def add_found(self, document_id, request):
        async def run(conn, document):
            self.require_status(document, "counting")
            locations = await conn.fetch(q.FIXED_LOCATIONS, document_id)
            if request.location_id not in {r["location_id"] for r in locations}:
                raise InventoryDocumentError(
                    "Discovered item is outside frozen locations", code="INVENTORY_OUTSIDE_SCOPE"
                )
            if document["product_ids"] and request.product_id not in document["product_ids"]:
                raise InventoryDocumentError(
                    "Discovered SKU is outside frozen filter", code="INVENTORY_OUTSIDE_SCOPE"
                )
            if not await conn.fetch(q.GET_PRODUCTS, [request.product_id]):
                raise InventoryDocumentError(
                    "Product not found", status_code=404, code="INVENTORY_PRODUCT_NOT_FOUND"
                )
            baseline = await self.repository.revision(conn, document_id, 1)
            pair = pair_evidence(baseline, request.product_id, request.location_id)
            _, conflicts = expected_and_conflicts(pair)
            item_id = await conn.fetchval(
                q.INSERT_ITEM,
                document_id,
                request.product_id,
                request.location_id,
                Decimal(0),
                True,
                dump(conflicts),
            )
            await conn.execute(q.BUMP_VERSION, document_id)
            return (
                dict(
                    document=await self.require_document(conn, document_id),
                    item=self.item_response(await self.require_item(conn, document_id, item_id)),
                ),
                document_id,
            )

        return await self.command("add_found", request, run, document_id)

    async def begin_count(self, document_id, item_id, request):
        async def run(conn, document):
            self.require_status(document, "counting")
            item = await self.require_item(conn, document_id, item_id)
            evidence = await self.repository.capture(
                conn, [item["location_id"]], [item["product_id"]]
            )
            attempt = await conn.fetchrow(
                q.CREATE_ATTEMPT,
                document_id,
                item_id,
                document["current_revision"],
                request.user_name,
                dump({"start": evidence}),
            )
            await conn.execute(q.SET_LATEST_ATTEMPT, document_id, item_id, attempt["attempt_id"])
            await conn.execute(q.BUMP_VERSION, document_id)
            return (
                dict(
                    document=await self.require_document(conn, document_id),
                    attempt=self.attempt_response(attempt),
                ),
                document_id,
            )

        return await self.command("begin_count", request, run, document_id, dict(item_id=item_id))

    async def save_count(self, document_id, item_id, attempt_id, request, *, complete):
        async def run(conn, document):
            self.require_status(document, "counting")
            item = await self.require_item(conn, document_id, item_id)
            attempt = await conn.fetchrow(q.GET_ATTEMPT, document_id, item_id, attempt_id)
            if attempt is None:
                raise InventoryDocumentError(
                    "Attempt not found", status_code=404, code="INVENTORY_ATTEMPT_NOT_FOUND"
                )
            if attempt["status"] != "in_progress" or item["latest_attempt_id"] != attempt_id:
                raise InventoryDocumentError(
                    "Attempt is not the current open attempt", code="INVENTORY_ATTEMPT_CONFLICT"
                )
            if complete:
                evidence = decode(attempt["evidence"])
                finish = await self.repository.capture(
                    conn, [item["location_id"]], [item["product_id"]]
                )
                conflicts = (
                    ["state_changed_during_count"]
                    if evidence_changed(evidence["start"], normalized(finish))
                    else []
                )
                evidence["finish"] = finish
                attempt = await conn.fetchrow(
                    q.FINISH_ATTEMPT,
                    attempt_id,
                    request.actual_quantity,
                    dump(evidence),
                    dump(conflicts),
                )
                await conn.execute(q.FINISH_ITEM, document_id, item_id)
            else:
                attempt = await conn.fetchrow(q.SAVE_PROGRESS, attempt_id, request.actual_quantity)
            await conn.execute(q.BUMP_VERSION, document_id)
            return (
                dict(
                    document=await self.require_document(conn, document_id),
                    attempt=self.attempt_response(attempt),
                ),
                document_id,
            )

        return await self.command(
            "complete_count" if complete else "save_progress",
            request,
            run,
            document_id,
            dict(item_id=item_id, attempt_id=attempt_id),
        )

    async def reconciliation(self, conn, document, author):
        document_id = document["document_id"]
        locations = await conn.fetch(q.FIXED_LOCATIONS, document_id)
        location_codes = {r["location_id"]: r["location_code"] for r in locations}
        current = normalized(
            await self.repository.capture(
                conn, [r["location_id"] for r in locations], document["product_ids"]
            )
        )
        initial = await self.repository.revision(conn, document_id, 1)
        initial_index, current_index = EvidenceIndex(initial), EvidenceIndex(current)
        lines = []
        for raw in await conn.fetch(q.ALL_ITEMS, document_id):
            item = dict(raw)
            old = initial_index.pair(item["product_id"], item["location_id"])
            now = current_index.pair(item["product_id"], item["location_id"])
            expected, conflicts = expected_and_conflicts(now)
            if location_codes[item["location_id"]] == settings.FBS_LOCATION_CODE:
                conflicts.append("configured_fbs_location_physical_boundary_unverified")
            movements = new_evidence_rows(old, now, "movements", movement_identity)
            events = new_evidence_rows(old, now, "kiz_events", lambda r: r["kiz_event_id"])
            conflicts += decode(item["initial_conflicts"])
            conflicts += decode(item["attempt_conflicts"]) or []
            if movements:
                conflicts.append("movements_since_start_physical_order_unverified")
                if any(r["movement_type"] == "ship" for r in movements):
                    conflicts.append("shipment_physical_time_unverified")
            if events or dump(old["kiz"]) != dump(now["kiz"]):
                conflicts.append("kiz_eligibility_changed")
            if evidence_changed(old, now) and not movements and not events:
                conflicts.append("state_change_without_new_ledger_evidence")
            attempt_evidence = decode(item["attempt_evidence"])
            changed_after_count = bool(
                attempt_evidence
                and "finish" in attempt_evidence
                and evidence_changed(attempt_evidence["finish"], now)
            )
            if changed_after_count:
                conflicts.append("state_changed_after_count")
            actual = item["actual_quantity"]
            lines.append(
                dict(
                    item_id=item["item_id"],
                    product_id=item["product_id"],
                    location_id=item["location_id"],
                    initial_expected_quantity=item["initial_expected_quantity"],
                    actual_quantity=actual,
                    initial_discrepancy=None
                    if actual is None
                    else actual - item["initial_expected_quantity"],
                    current_expected_quantity=expected,
                    movements_since_start=movements,
                    kiz_events_since_start=events,
                    conflicts=sorted(set(conflicts)),
                    ambiguous=bool(conflicts),
                    requires_recount=bool(
                        item["needs_recount"]
                        or changed_after_count
                        or decode(item["attempt_conflicts"])
                    ),
                    safe_for_apply=False,
                )
            )
        revision_no = document["current_revision"] + 1
        current["reconciliation_lines"] = normalized(lines)
        await conn.execute(
            q.INSERT_REVISION,
            document_id,
            revision_no,
            "reconciliation",
            datetime.fromisoformat(current["observed_at"]),
            author,
            dump(current),
        )
        await conn.execute(q.SET_REVISION, document_id, revision_no)
        return dict(
            document=await self.require_document(conn, document_id),
            revision_no=revision_no,
            observed_at=current["observed_at"],
            preliminary_only=True,
            lines=lines,
        )

    async def reconcile(self, document_id, request):
        async def run(conn, document):
            self.require_status(document, "counting", "pending_approval")
            return await self.reconciliation(conn, document, request.user_name), document_id

        return await self.command("reconcile", request, run, document_id)

    async def submit(self, document_id, request):
        async def run(conn, document):
            self.require_status(document, "counting")
            items = await conn.fetch(q.ALL_ITEMS, document_id)
            if any(r["needs_recount"] or r["attempt_status"] != "completed" for r in items):
                raise InventoryDocumentError(
                    "All lines must have a completed count", code="INVENTORY_INCOMPLETE_COUNT"
                )
            # Fresh evidence and submission share one coherent transaction/snapshot.
            refreshed = await self.reconciliation(conn, document, request.user_name)
            await conn.execute(q.SUBMIT, document_id)
            refreshed["document"] = await self.require_document(conn, document_id)
            return refreshed, document_id

        return await self.command("submit", request, run, document_id)

    async def return_to_count(self, document_id, request):
        async def run(conn, document):
            self.require_status(document, "pending_approval")
            item_ids = {r["item_id"] for r in await conn.fetch(q.ALL_ITEMS, document_id)}
            selected = set(request.item_ids) if request.item_ids is not None else item_ids
            if not selected.issubset(item_ids):
                raise InventoryDocumentError(
                    "Recount item is outside this document",
                    code="INVENTORY_ITEM_NOT_FOUND",
                    status_code=404,
                )
            await conn.execute(q.REQUIRE_RECOUNT, document_id, sorted(selected))
            await conn.execute(q.RETURN_TO_COUNT, document_id)
            return await self.require_document(conn, document_id), document_id

        return await self.command("return_to_count", request, run, document_id)

    async def close(self, document_id, request):
        async def run(conn, document):
            self.require_status(document, "draft", "counting", "pending_approval")
            await conn.execute(q.RELEASE_CLAIMS, document_id)
            await conn.execute(q.CLOSE, document_id)
            return await self.require_document(conn, document_id), document_id

        return await self.command("close_without_adjustment", request, run, document_id)

    async def get(self, document_id):
        async with self.repository.pool.acquire() as conn:
            return await self.require_document(conn, document_id)

    async def list_documents(self, status=None, warehouse_location_id=None, limit=100, offset=0):
        async with self.repository.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                rows = await conn.fetch(
                    q.LIST_DOCUMENTS, status, warehouse_location_id, limit, offset
                )
                items = []
                for row in rows:
                    item = dict(row)
                    item["locations"] = decode(item["locations"])
                    items.append(item)
                total = await conn.fetchval(q.COUNT_DOCUMENTS, status, warehouse_location_id)
                return dict(items=items, total=total, limit=limit, offset=offset)

    async def page(self, document_id, kind, limit=100, offset=0, item_id=None):
        async with self.repository.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                await self.require_document(conn, document_id)
                if kind == "attempts":
                    await self.require_item(conn, document_id, item_id)
                    rows = await conn.fetch(q.LIST_ATTEMPTS, document_id, item_id, limit, offset)
                    total = await conn.fetchval(q.COUNT_ATTEMPTS, document_id, item_id)
                    items = [self.attempt_response(r) for r in rows]
                else:
                    sql, count = {
                        "items": (q.LIST_ITEMS, q.COUNT_ITEMS),
                        "history": (q.LIST_HISTORY, q.COUNT_HISTORY),
                        "revisions": (q.LIST_REVISIONS, q.COUNT_REVISIONS),
                    }[kind]
                    rows = await conn.fetch(sql, document_id, limit, offset)
                    total = await conn.fetchval(count, document_id)
                    if kind == "items":
                        items = [self.item_response(dict(r)) for r in rows]
                    else:
                        items = [dict(r) for r in rows]
                        for r in items:
                            field = "details" if kind == "history" else "evidence"
                            r[field] = decode(r[field])
                return dict(items=items, total=total, limit=limit, offset=offset)

    async def fixed_scope(self, document_id):
        async with self.repository.pool.acquire() as conn:
            await self.require_document(conn, document_id)
            return [dict(r) for r in await conn.fetch(q.FIXED_LOCATIONS, document_id)]
