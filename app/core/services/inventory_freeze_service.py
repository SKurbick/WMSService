"""Persistent physical-window workflow; SQL guards enforce writer admission."""

from app.core.inventory_document_errors import InventoryDocumentError
from app.core.services.inventory_document_service import dump
from app.infrastructure.database.queries import inventory_documents as documents
from app.infrastructure.database.queries import inventory_freeze as sql
from app.infrastructure.database.repositories.inventory_document_repository import decode
from app.shared.config import settings


def freeze_error(message, code="INVENTORY_FREEZE_CONFLICT"):
    raise InventoryDocumentError(message, code=code)


async def lock_active_freeze(conn, document_id):
    if not await conn.fetchval("SELECT to_regclass('wms.inventory_freezes') IS NOT NULL"):
        return None
    freeze = await conn.fetchrow(sql.ACTIVE, document_id)
    if freeze is None:
        return None
    ids = [r["location_id"] for r in await conn.fetch(documents.FIXED_LOCATIONS, document_id)]
    fences = await conn.fetch(sql.LOCK_FENCES, ids)
    if len(fences) != len(ids) or any(r["freeze_id"] != freeze["freeze_id"] for r in fences):
        freeze_error("Freeze does not cover the complete document")
    return await conn.fetchrow(
        "SELECT * FROM wms.inventory_freezes WHERE freeze_id=$1 FOR UPDATE NOWAIT",
        freeze["freeze_id"],
    )


def freeze_token(freeze):
    return {"freeze_id": freeze["freeze_id"], "epoch": freeze["epoch"]}


async def lock_task_admission(conn):
    if not await conn.fetchval(
        "SELECT pg_try_advisory_xact_lock(hashtextextended('inventory-task-admission',0))"
    ):
        freeze_error(
            "Warehouse task intent is being changed; retry the command",
            "INVENTORY_TASK_ADMISSION_BUSY",
        )


async def validate_fixed_locations(service, conn, document, locations):
    # Validate the fixed IDs, not the draft selection expanded against today's
    # descendants. A newly created child is not part of this document's scope.
    spec = dict(
        document,
        mode="partial",
        locations=[
            dict(location_id=row["location_id"], include_descendants=False) for row in locations
        ],
    )
    return await service.resolve_scope(conn, spec)


def public_freeze(freeze):
    if freeze is None:
        return None
    return {key: value for key, value in dict(freeze).items() if key != "baseline"}


class InventoryFreezeService:
    def __init__(self, documents_service):
        self.documents = documents_service

    async def event(self, conn, freeze, action, author, details):
        await conn.execute(sql.EVENT, freeze["freeze_id"], action, author, dump(details))

    async def begin(self, document_id, request):
        if not settings.INVENTORY_FREEZE_ENABLED:
            freeze_error("Inventory freeze is disabled", "INVENTORY_FREEZE_DISABLED")

        async def run(conn, document):
            self.documents.require_status(document, "counting")
            if not await conn.fetchval(
                "SELECT to_regclass('wms.inventory_location_fences') IS NOT NULL"
            ):
                raise InventoryDocumentError(
                    "Inventory freeze migration is not installed",
                    status_code=503,
                    code="INVENTORY_SCHEMA_NOT_READY",
                )
            locations = await conn.fetch(documents.FIXED_LOCATIONS, document_id)
            if (
                document["mode"] != "partial"
                or not 0 < len(locations) <= settings.INVENTORY_PILOT_MAX_LOCATIONS
            ):
                freeze_error(
                    "Physical pilot supports only small partial documents",
                    "INVENTORY_PILOT_SCOPE_UNSUPPORTED",
                )
            if await conn.fetchrow(sql.ACTIVE, document_id):
                freeze_error("Document already has an active freeze")
            current = await validate_fixed_locations(self.documents, conn, document, locations)
            ids = [r["location_id"] for r in locations]
            if set(ids) != {r["location_id"] for r in current}:
                freeze_error("Document hierarchy has changed")
            fences = await conn.fetch(sql.LOCK_FENCES, ids)
            if len(fences) != len(ids) or any(r["freeze_id"] is not None for r in fences):
                freeze_error("Location is already frozen or its fence is absent")
            freeze = await conn.fetchrow(sql.CREATE, document_id, request.user_name)
            await conn.execute(sql.CLAIM, ids, freeze["freeze_id"])
            await self.event(conn, freeze, "freezing", request.user_name, {})
            await conn.execute(documents.BUMP_VERSION, document_id)
            return (
                dict(
                    document=await self.documents.require_document(conn, document_id),
                    freeze=public_freeze(freeze),
                ),
                document_id,
            )

        return await self.documents.command("begin_freeze", request, run, document_id)

    async def confirm(self, document_id, request):
        if not settings.INVENTORY_FREEZE_ENABLED:
            freeze_error("Inventory freeze is disabled", "INVENTORY_FREEZE_DISABLED")

        async def run(conn, document):
            self.documents.require_status(document, "counting")
            freeze = await lock_active_freeze(conn, document_id)
            if freeze is None or freeze["state"] not in ("freezing", "invalidated"):
                freeze_error("Freeze must be freezing or invalidated")
            ids = [
                r["location_id"] for r in await conn.fetch(documents.FIXED_LOCATIONS, document_id)
            ]
            await lock_task_admission(conn)
            conflicts = await conn.fetch(sql.CONFLICTING_TASKS, ids)
            if conflicts:
                raise InventoryDocumentError(
                    "Resolve warehouse tasks before confirming the window",
                    code="INVENTORY_FREEZE_OPERATIONS_PENDING",
                    details={"tasks": [dict(r) for r in conflicts]},
                )
            baseline = await self.documents.repository.capture(conn, ids, document["product_ids"])
            freeze = await conn.fetchrow(
                sql.CONFIRM,
                freeze["freeze_id"],
                request.user_name,
                request.confirmation,
                dump(baseline),
            )
            await conn.execute(sql.CANCEL_OPEN, document_id)
            await conn.execute(sql.RECOUNT_ALL, document_id)
            await self.event(
                conn,
                freeze,
                "frozen",
                request.user_name,
                dict(
                    confirmation=request.confirmation,
                    technical_proof_of_physical_time=False,
                    epoch=freeze["epoch"],
                ),
            )
            await conn.execute(documents.BUMP_VERSION, document_id)
            return (
                dict(
                    document=await self.documents.require_document(conn, document_id),
                    freeze=public_freeze(freeze),
                ),
                document_id,
            )

        return await self.documents.command(
            "confirm_freeze", request, run, document_id, isolation="read_committed"
        )

    async def release(self, document_id, request):
        async def run(conn, document):
            self.documents.require_status(document, "counting", "pending_approval")
            freeze = await lock_active_freeze(conn, document_id)
            if freeze is None:
                freeze_error("No active freeze")
            await conn.execute(sql.RELEASE_FENCES, freeze["freeze_id"])
            await conn.execute(sql.RELEASE, freeze["freeze_id"])
            await conn.execute(sql.CANCEL_OPEN, document_id)
            await conn.execute(sql.RECOUNT_ALL, document_id)
            await self.event(
                conn,
                freeze,
                "released_without_adjustment",
                request.user_name,
                {"reason": request.reason},
            )
            await conn.execute(documents.BUMP_VERSION, document_id)
            return (
                dict(
                    document=await self.documents.require_document(conn, document_id),
                    freeze=public_freeze(await conn.fetchrow(sql.LATEST, document_id)),
                ),
                document_id,
            )

        return await self.documents.command("release_freeze", request, run, document_id)

    async def status(self, document_id, limit=100, offset=0):
        async with self.documents.repository.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                await self.documents.require_document(conn, document_id)
                if not await conn.fetchval(
                    "SELECT to_regclass('wms.inventory_freezes') IS NOT NULL"
                ):
                    raise InventoryDocumentError(
                        "Inventory freeze migration is not installed",
                        status_code=503,
                        code="INVENTORY_SCHEMA_NOT_READY",
                    )
                freeze = await conn.fetchrow(sql.LATEST, document_id)
                events = []
                total = 0
                conflicts = []
                if freeze:
                    ids = [
                        r["location_id"]
                        for r in await conn.fetch(documents.FIXED_LOCATIONS, document_id)
                    ]
                    conflicts = [dict(r) for r in await conn.fetch(sql.CONFLICTING_TASKS, ids)]
                    for row in await conn.fetch(sql.EVENTS, freeze["freeze_id"], limit, offset):
                        row = dict(row)
                        row["details"] = decode(row["details"])
                        events.append(row)
                    total = await conn.fetchval(
                        "SELECT count(*) FROM wms.inventory_freeze_events WHERE freeze_id=$1",
                        freeze["freeze_id"],
                    )
                return dict(
                    freeze=public_freeze(freeze),
                    conflicting_operations=conflicts[:100],
                    conflicts_truncated=len(conflicts) > 100,
                    events=dict(items=events, total=total, limit=limit, offset=offset),
                )
