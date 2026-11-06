"""Identity observations on existing documents and controlled, atomic corrections."""
from contextlib import asynccontextmanager
from decimal import Decimal

from app.core.inventory_document_errors import InventoryDocumentError
from app.core.services.inventory_document_service import dump, normalized
from app.core.services.inventory_freeze_service import lock_active_freeze, freeze_token
from app.infrastructure.database.queries import inventory_documents as docs
from app.infrastructure.database.queries import inventory_identities as sql
from app.infrastructure.database.repositories.inventory_document_repository import decode


def conflict(reason, **details):
    return dict(reason=reason, **details)


class TransactionPool:
    """Bind existing controlled services to the caller's transaction, never another pool."""

    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class InventoryIdentityService:
    def __init__(self, documents):
        self.documents = documents

    async def config(self, conn, document_id):
        if not await conn.fetchval(
            "SELECT to_regclass('wms.inventory_identity_config') IS NOT NULL"
        ):
            return None
        return await conn.fetchrow(sql.CONFIG, document_id)

    async def capture(self, conn, document):
        ids = [
            r["location_id"]
            for r in await conn.fetch(docs.FIXED_LOCATIONS, document["document_id"])
        ]
        containers = [dict(r) for r in await conn.fetch(sql.CONTAINERS, ids)]
        cids = [r["container_id"] for r in containers]
        evidence = dict(
            containers=containers,
            kiz=[dict(r) for r in await conn.fetch(sql.KIZ, ids, cids, document["product_ids"])],
            contents=[dict(r) for r in await conn.fetch(sql.CONTENTS, cids)],
            stocks=[dict(r) for r in await conn.fetch(sql.STOCK, ids)],
        )
        if any(len(rows) > 100000 for rows in evidence.values()):
            raise InventoryDocumentError(
                "Identity evidence limit exceeded", code="INVENTORY_EVIDENCE_LIMIT"
            )
        return normalized(evidence)

    async def enable(self, document_id, request):
        async def run(conn, document):
            self.documents.require_status(document, "draft")
            if not await conn.fetchval(
                "SELECT to_regclass('wms.inventory_identity_config') IS NOT NULL"
            ):
                raise InventoryDocumentError(
                    "Install Stage 2B migration", status_code=503, code="INVENTORY_SCHEMA_NOT_READY"
                )
            if await self.config(conn, document_id):
                raise InventoryDocumentError(
                    "Identity accounting already configured", code="INVENTORY_IDENTITY_CONFIGURED"
                )
            await conn.execute(
                "INSERT INTO wms.inventory_identity_config VALUES($1,$2)",
                document_id,
                request.containers,
            )
            await conn.execute(docs.BUMP_VERSION, document_id)
            return await self.documents.require_document(conn, document_id), document_id

        return await self.documents.command("identity_accounting", request, run, document_id)

    async def snapshot(self, conn, document):
        if await self.config(conn, document["document_id"]):
            await conn.execute(
                "INSERT INTO wms.inventory_identity_snapshots VALUES($1,$2::jsonb)",
                document["document_id"],
                dump(await self.capture(conn, document)),
            )

    async def begin(self, document_id, request):
        async def run(conn, document):
            self.documents.require_status(document, "counting")
            if not await self.config(conn, document_id):
                raise InventoryDocumentError(
                    "Enable identity accounting in draft", code="INVENTORY_IDENTITY_DISABLED"
                )
            freeze = await lock_active_freeze(conn, document_id)
            token = freeze_token(freeze) if freeze and freeze["state"] == "frozen" else None
            if freeze and not token:
                raise InventoryDocumentError(
                    "Confirm frozen window before final scan", code="INVENTORY_FREEZE_CONFLICT"
                )
            await conn.execute(
                "UPDATE wms.inventory_identity_counts SET status='superseded' WHERE document_id=$1 AND status='in_progress'",
                document_id,
            )
            row = await conn.fetchrow(
                """INSERT INTO wms.inventory_identity_counts
                (document_id,revision_no,author,start_evidence,freeze_token)
                VALUES($1,$2,$3,$4::jsonb,$5::jsonb) RETURNING count_id""",
                document_id,
                document["current_revision"],
                request.user_name,
                dump(await self.capture(conn, document)),
                dump(token) if token else None,
            )
            await conn.execute(docs.BUMP_VERSION, document_id)
            return (
                dict(
                    document=await self.documents.require_document(conn, document_id),
                    count_id=row["count_id"],
                ),
                document_id,
            )

        return await self.documents.command(
            "identity_count_begin", request, run, document_id, isolation="read_committed"
        )

    async def require_count(self, conn, document_id, count_id):
        row = await conn.fetchrow(sql.LATEST, document_id)
        if row is None or row["count_id"] != count_id or row["status"] != "in_progress":
            raise InventoryDocumentError(
                "Identity count is not current/open", code="INVENTORY_IDENTITY_COUNT_CLOSED"
            )
        return row

    async def scan(self, document_id, count_id, request):
        async def run(conn, document):
            self.documents.require_status(document, "counting")
            await self.require_count(conn, document_id, count_id)
            if not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM wms.inventory_document_locations WHERE document_id=$1 AND location_id=$2)",
                document_id,
                request.location_id,
            ):
                raise InventoryDocumentError(
                    "Scan outside fixed scope", code="INVENTORY_SCOPE_CONFLICT"
                )
            config = await self.config(conn, document_id)
            if (request.kind == "container" or request.holder_qr) and not config["containers"]:
                raise InventoryDocumentError(
                    "Container accounting not enabled", code="INVENTORY_IDENTITY_CONTAINER_DISABLED"
                )
            row = await conn.fetchrow(
                """INSERT INTO wms.inventory_identity_scans
              (document_id,count_id,author,kind,code,location_id,holder_qr,contents)
              VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb) RETURNING scan_id""",
                document_id,
                count_id,
                request.user_name,
                request.kind,
                request.kiz_code if request.kind == "kiz" else request.container_qr,
                request.location_id,
                request.holder_qr,
                dump([r.model_dump() for r in request.contents]),
            )
            await conn.execute(docs.BUMP_VERSION, document_id)
            return (
                dict(
                    document=await self.documents.require_document(conn, document_id),
                    scan_id=row["scan_id"],
                ),
                document_id,
            )

        return await self.documents.command(
            "identity_scan", request, run, document_id, extra={"count_id": count_id}
        )

    async def plan(self, conn, document, evidence, scans, config):
        """Only conserved physical transfers; never infer missing identities or quantity origin."""
        conflicts = []
        actions = []
        unid_changes = {}
        locations = {
            r["location_id"]: r["location_code"]
            for r in await conn.fetch(docs.FIXED_LOCATIONS, document["document_id"])
        }
        containers = {r["container_id"]: r for r in evidence["containers"]}
        byqr = {r["qr_code"]: r for r in containers.values()}
        if any(
            r["container_code"] is not None and r["container_code"] not in byqr
            for r in evidence["stocks"]
        ):
            conflicts.append(conflict("orphan_container_projection"))
        scan_kiz = {}
        scan_containers = {}
        for scan in scans:
            target = scan_kiz if scan["kind"] == "kiz" else scan_containers
            if scan["code"] in target:
                conflicts.append(conflict("duplicate_scan", kind=scan["kind"], code=scan["code"]))
            target[scan["code"]] = scan
        expected = {
            r["kiz_code"]: r
            for r in evidence["kiz"]
            if r["lifecycle_status"] == "active"
            and (config["containers"] or r["container_id"] is None)
        }
        # Look up scanned identities globally to distinguish unknown, closed and outside scope.
        known = {
            r["kiz_code"]: dict(r)
            for r in await conn.fetch(
                "SELECT * FROM wms.kiz WHERE kiz_code=ANY($1::text[])", list(scan_kiz)
            )
        }
        for code in sorted(expected.keys() - scan_kiz.keys()):
            conflicts.append(conflict("missing_kiz", code=code))
        for code, scan in scan_kiz.items():
            row = known.get(code)
            if not row:
                conflicts.append(conflict("unknown_kiz", code=code))
                continue
            if row["lifecycle_status"] != "active" or row["closed_at"] is not None:
                conflicts.append(
                    conflict(
                        "kiz_lifecycle_requires_explicit_resolution",
                        code=code,
                        status=row["lifecycle_status"],
                    )
                )
                continue
            if code not in expected:
                conflicts.append(conflict("kiz_outside_scope_or_sku", code=code))
                continue
            if scan["holder_qr"] and scan["holder_qr"] not in byqr:
                conflicts.append(conflict("unknown_container_holder", code=code))
                continue
            if scan["holder_qr"]:
                holder_scan = scan_containers.get(scan["holder_qr"])
                if not holder_scan or holder_scan["location_id"] != scan["location_id"]:
                    conflicts.append(
                        conflict("container_holder_scan_missing_or_wrong_location", code=code)
                    )

        changed_holders = set()
        if config["containers"]:
            for qr in sorted(byqr.keys() - scan_containers.keys()):
                conflicts.append(conflict("missing_container", qr=qr))
            for qr in sorted(scan_containers.keys() - byqr.keys()):
                conflicts.append(conflict("container_unknown_or_outside_scope", qr=qr))
            for qr, c in byqr.items():
                scan = scan_containers.get(qr)
                if not scan:
                    continue
                if c["parent_container_id"] is not None:
                    conflicts.append(
                        conflict("nested_container_not_supported_by_current_db_contract", qr=qr)
                    )
                if c["location_id"] != c["effective_location_id"]:
                    conflicts.append(conflict("container_hierarchy_location_conflict", qr=qr))
                if c["status"] == "blocked":
                    conflicts.append(conflict("blocked_container", qr=qr))
                nested = c["parent_container_id"] is not None or any(
                    x["parent_container_id"] == c["container_id"] for x in containers.values()
                )
                if c["parent_container_id"]:
                    parent = containers.get(c["parent_container_id"])
                    parent_scan = scan_containers.get(parent["qr_code"]) if parent else None
                    if not parent_scan or parent_scan["location_id"] != scan["location_id"]:
                        conflicts.append(conflict("nested_holder_location_conflict", qr=qr))
                current_contents = [
                    r for r in evidence["contents"] if r["container_id"] == c["container_id"]
                ]
                if any(r["batch_number"] is not None for r in current_contents):
                    conflicts.append(conflict("batch_container_requires_resolution", qr=qr))
                quantities = {r["product_id"]: Decimal(r["quantity"]) for r in current_contents}
                if len(quantities) != len(current_contents):
                    conflicts.append(conflict("multi_batch_container", qr=qr))
                actual = {r["product_id"]: Decimal(r["quantity"]) for r in decode(scan["contents"])}
                if document["product_ids"] and (set(actual) | set(quantities)) - set(
                    document["product_ids"]
                ):
                    conflicts.append(conflict("container_contains_unselected_sku", qr=qr))
                # Projection drift is never repaired from guessed contents.
                physical = [r for r in evidence["stocks"] if r["container_code"] == qr]
                if (
                    any(
                        r["status"] != "available" or r["batch_number"] is not None
                        for r in physical
                    )
                    or {r["product_id"]: Decimal(r["quantity"]) for r in physical} != quantities
                    or len(physical) != len(quantities)
                ):
                    conflicts.append(conflict("container_projection_conflict", qr=qr))
                if any(r["location_id"] != c["effective_location_id"] for r in physical):
                    conflicts.append(conflict("container_projection_location_conflict", qr=qr))
                if c["effective_location_id"] != scan["location_id"]:
                    if nested:
                        conflicts.append(conflict("nested_container_move_unsupported", qr=qr))
                    else:
                        actions.append(
                            dict(
                                kind="container_move",
                                container_id=c["container_id"],
                                to_location_code=locations[scan["location_id"]],
                            )
                        )
                for pid in sorted(set(quantities) | set(actual)):
                    delta = actual.get(pid, Decimal(0)) - quantities.get(pid, Decimal(0))
                    before = {
                        k
                        for k, r in expected.items()
                        if r["container_id"] == c["container_id"] and r["product_id"] == pid
                    }
                    after = {
                        k
                        for k, s in scan_kiz.items()
                        if s["holder_qr"] == qr and known.get(k, {}).get("product_id") == pid
                    }
                    incoming = after - before
                    outgoing = before - after
                    if delta == 0 and (incoming or outgoing):
                        conflicts.append(
                            conflict("identity_exchange_requires_resolution", qr=qr, product_id=pid)
                        )
                    if not delta:
                        continue
                    codes = incoming if delta > 0 else outgoing
                    if (
                        nested
                        or c["status"] not in ("open", "empty")
                        or c["effective_location_id"] != scan["location_id"]
                    ):
                        conflicts.append(conflict("container_content_operation_unsupported", qr=qr))
                        continue
                    if (
                        (delta > 0 and outgoing)
                        or (delta < 0 and incoming)
                        or abs(delta) < len(codes)
                    ):
                        conflicts.append(conflict("container_count_identity_mismatch", qr=qr))
                        continue
                    for code in codes:
                        k = known.get(code)
                        s = scan_kiz.get(code)
                        if (
                            not k
                            or not s
                            or s["location_id"] != scan["location_id"]
                            or (
                                delta > 0
                                and (
                                    k["container_id"] is not None
                                    or k["location_id"] != scan["location_id"]
                                )
                            )
                            or (delta < 0 and s["holder_qr"] is not None)
                        ):
                            conflicts.append(conflict("content_kiz_holder_ambiguous", code=code))
                    actions.append(
                        dict(
                            kind="fill" if delta > 0 else "extract",
                            container_id=c["container_id"],
                            product_id=pid,
                            quantity=str(abs(delta)),
                            kiz_codes=sorted(codes),
                        )
                    )
                    changed_holders.update(codes)
                    pair = (pid, scan["location_id"])
                    unid_changes[pair] = (
                        unid_changes.get(pair, Decimal(0))
                        - delta
                        + (len(codes) if delta > 0 else -len(codes))
                    )
        elif evidence["containers"]:
            conflicts.append(conflict("containers_not_in_identity_count"))

        for code, row in expected.items():
            scan = scan_kiz.get(code)
            if not scan or code in changed_holders:
                continue
            current_qr = (
                containers[row["container_id"]]["qr_code"]
                if row["container_id"] in containers
                else None
            )
            if current_qr != scan["holder_qr"]:
                conflicts.append(conflict("holder_change_requires_resolution", code=code))
                continue
            if current_qr:
                if scan_containers.get(current_qr, {}).get("location_id") != scan["location_id"]:
                    conflicts.append(conflict("container_kiz_location_mismatch", code=code))
            elif row["location_id"] != scan["location_id"]:
                if row["location_id"] not in locations:
                    conflicts.append(conflict("source_outside_frozen_scope", code=code))
                    continue
                actions.append(
                    dict(
                        kind="kiz_transfer",
                        product_id=row["product_id"],
                        from_location_code=locations[row["location_id"]],
                        to_location_code=locations[scan["location_id"]],
                        kiz_codes=[code],
                    )
                )
        # Batches/null-empty aliases remain unsupported for identity physical operations.
        for (product_id, location_id), change in unid_changes.items():
            physical = sum(
                (
                    Decimal(r["quantity"])
                    for r in evidence["stocks"]
                    if r["product_id"] == product_id
                    and r["location_id"] == location_id
                    and r["container_code"] is None
                    and r["status"] == "available"
                    and r["batch_number"] is None
                ),
                Decimal(0),
            )
            identified = sum(
                r["product_id"] == product_id
                and r["location_id"] == location_id
                and r["container_id"] is None
                and r["lifecycle_status"] == "active"
                for r in evidence["kiz"]
            )
            if physical - identified + change < 0:
                conflicts.append(
                    conflict(
                        "insufficient_loose_stock_for_container_reconciliation",
                        product_id=product_id,
                        location_id=location_id,
                    )
                )
        contents_products = {
            r["product_id"] for scan in scan_containers.values() for r in decode(scan["contents"])
        }
        known_products = {
            r["id"] for r in await conn.fetch(docs.GET_PRODUCTS, list(contents_products))
        }
        for product_id in sorted(contents_products - known_products):
            conflicts.append(conflict("unknown_container_product", product_id=product_id))
        actions.sort(
            key=lambda a: {"extract": 0, "kiz_transfer": 1, "container_move": 2, "fill": 3}[
                a["kind"]
            ]
        )
        if actions and any(
            r["batch_number"] is not None or r["container_code"] == "" for r in evidence["stocks"]
        ):
            conflicts.append(conflict("batch_or_empty_key_requires_resolution"))
        return normalized(
            dict(
                conflicts=conflicts,
                actions=actions,
                unidentified_changes=[
                    dict(product_id=p, location_id=location_id, quantity=str(q))
                    for (p, location_id), q in sorted(unid_changes.items())
                ],
                resolution=[
                    "Return document to counting if submitted",
                    "Release freeze explicitly before external controlled resolution",
                    "Resolve identity/lifecycle/holder with existing controlled WMS operations, restore freeze and repeat all final counts",
                ]
                if conflicts
                else [],
            )
        )

    async def complete(self, document_id, count_id, request):
        async def run(conn, document):
            self.documents.require_status(document, "counting")
            count = await self.require_count(conn, document_id, count_id)
            freeze = await lock_active_freeze(conn, document_id)
            now = await self.capture(conn, document)
            scans = [dict(r) for r in await conn.fetch(sql.SCANS, count_id)]
            plan = await self.plan(conn, document, now, scans, await self.config(conn, document_id))
            if (
                dump(now) != dump(decode(count["start_evidence"]))
                or decode(count["freeze_token"]) != (freeze_token(freeze) if freeze else None)
                or (freeze and freeze["state"] != "frozen")
            ):
                plan["conflicts"].append(conflict("state_changed_during_identity_count"))
            if plan["conflicts"] and not plan["resolution"]:
                plan["resolution"] = [
                    "Restore a confirmed freeze window and repeat the identity count; preserve this attempt as evidence"
                ]
            await conn.execute(
                """UPDATE wms.inventory_identity_counts SET status='completed',completed_at=clock_timestamp(),
                end_evidence=$2::jsonb,result=$3::jsonb WHERE count_id=$1""",
                count_id,
                dump(now),
                dump(plan),
            )
            await conn.execute(docs.BUMP_VERSION, document_id)
            return (
                dict(
                    document=await self.documents.require_document(conn, document_id),
                    count_id=count_id,
                    result=plan,
                ),
                document_id,
            )

        return await self.documents.command(
            "identity_count_complete",
            request,
            run,
            document_id,
            extra={"count_id": count_id},
            isolation="read_committed",
        )

    async def require_complete(self, conn, document):
        if await self.config(conn, document["document_id"]):
            row = await conn.fetchrow(sql.LATEST, document["document_id"])
            returned = await conn.fetchval(
                "SELECT max(occurred_at) FROM wms.inventory_document_history WHERE document_id=$1 AND action='return_to_count'",
                document["document_id"],
            )
            if (
                not row
                or row["status"] != "completed"
                or (returned and row["started_at"] <= returned)
            ):
                raise InventoryDocumentError(
                    "Complete identity count before submission",
                    code="INVENTORY_INCOMPLETE_IDENTITY_COUNT",
                )

    async def observation(self, conn, document, freeze):
        config = await self.config(conn, document["document_id"])
        if not config:
            return None
        row = await conn.fetchrow(sql.LATEST, document["document_id"])
        current = await self.capture(conn, document)
        blockers = []
        if not row or row["status"] != "completed":
            blockers.append("identity_count_incomplete")
            plan = dict(conflicts=[], actions=[], unidentified_changes=[])
        else:
            plan = decode(row["result"])
            if decode(row["freeze_token"]) != freeze_token(freeze):
                blockers.append("identity_count_not_in_current_freeze_epoch")
            if dump(decode(row["end_evidence"])) != dump(current):
                blockers.append("identity_state_changed_after_count")
            blockers.extend(c["reason"] for c in plan["conflicts"])
        return dict(
            count_id=row["count_id"] if row else None,
            current=current,
            plan=plan,
            blockers=sorted(set(blockers)),
        )

    async def get(self, document_id, limit=100, offset=0):
        async with self.documents.repository.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                await self.documents.require_document(conn, document_id)
                if not await self.config(conn, document_id):
                    return dict(enabled=False)
                snapshot = await conn.fetchval(
                    "SELECT evidence FROM wms.inventory_identity_snapshots WHERE document_id=$1",
                    document_id,
                )
                attempts = []
                for row in await conn.fetch(sql.COUNTS, document_id, limit, offset):
                    data = dict(row)
                    for key in ("start_evidence", "end_evidence", "result", "freeze_token"):
                        data[key] = decode(data[key])
                    data["scans"] = [
                        dict(r, contents=decode(r["contents"]))
                        for r in await conn.fetch(sql.SCANS, row["count_id"])
                    ]
                    attempts.append(data)
                total = await conn.fetchval(
                    "SELECT count(*) FROM wms.inventory_identity_counts WHERE document_id=$1",
                    document_id,
                )
                return normalized(
                    dict(
                        enabled=True,
                        snapshot=decode(snapshot),
                        attempts=attempts,
                        total=total,
                        limit=limit,
                        offset=offset,
                    )
                )

    async def execute(self, conn, document, application_id, identity, author):
        from app.core.services.kiz_transfer_service import KizTransferService
        from app.core.services.kiz_operation_idempotency_service import (
            KizOperationIdempotencyService,
        )
        from app.infrastructure.database.repositories.kiz_operation_repository import (
            KizOperationRepository,
        )
        from app.infrastructure.database.repositories.kiz_transfer_repository import (
            KizTransferRepository,
        )
        from app.core.schemas.kiz_operations import KizTransferRequest
        from app.core.services.container_move_service import ContainerMoveService
        from app.core.services.container_fill_service import ContainerFillService
        from app.core.services.container_extract_service import ContainerExtractService
        from app.infrastructure.database.repositories.container_operation_repository import (
            ContainerOperationRepository,
        )
        from app.core.services.container_operation_idempotency_service import (
            ContainerOperationIdempotencyService,
        )
        from app.core.schemas.container_operations import (
            ContainerMoveRequest,
            ContainerFillRequest,
            ContainerExtractRequest,
        )

        pool = TransactionPool(conn)
        repository = ContainerOperationRepository(pool)
        idem = ContainerOperationIdempotencyService(repository)
        for no, action in enumerate(identity["plan"]["actions"], 1):
            await conn.execute(
                "INSERT INTO wms.inventory_identity_actions(application_id,action_no,intent) VALUES($1,$2,$3::jsonb)",
                application_id,
                no,
                dump(action),
            )
            await conn.execute(
                "SELECT set_config('wms.inventory_identity_action',$1,true)", str(no)
            )
            common = dict(
                source_system="inventory_document",
                external_operation_id=f"{application_id}:identity:{no}",
                author=author,
            )
            if action["kind"] == "kiz_transfer":
                service = KizTransferService(
                    KizTransferRepository(pool),
                    KizOperationIdempotencyService(KizOperationRepository()),
                )
                data = KizTransferRequest(
                    **common,
                    items=[
                        dict(
                            external_line_id="identity",
                            product_id=action["product_id"],
                            from_location_code=action["from_location_code"],
                            to_location_code=action["to_location_code"],
                            quantity=len(action["kiz_codes"]),
                            kiz_codes=action["kiz_codes"],
                        )
                    ],
                )
                result = (await service.transfer(data)).model_dump(mode="json")
                refs = [i["movement_ref"] for i in result["items"]]
            else:
                if action["kind"] == "container_move":
                    service = ContainerMoveService(repository, idem)
                    result = (
                        await service.move(
                            ContainerMoveRequest(
                                **common,
                                container_id=action["container_id"],
                                to_location_code=action["to_location_code"],
                            )
                        )
                    ).model_dump(mode="json")
                else:
                    cls, method, model = (
                        (ContainerFillService, "fill", ContainerFillRequest)
                        if action["kind"] == "fill"
                        else (ContainerExtractService, "extract", ContainerExtractRequest)
                    )
                    data = model(
                        **common,
                        container_id=action["container_id"],
                        items=[
                            dict(
                                external_line_id="identity",
                                product_id=action["product_id"],
                                quantity=action["quantity"],
                                kiz_codes=action["kiz_codes"],
                            )
                        ],
                    )
                    result = (await getattr(cls(repository, idem), method)(data)).model_dump(
                        mode="json"
                    )
                refs = [ref for i in result["items"] for ref in i["movement_refs"]]
            result["movement_refs"] = refs
            await conn.execute(
                "UPDATE wms.inventory_identity_actions SET result=$3::jsonb WHERE application_id=$1 AND action_no=$2",
                application_id,
                no,
                dump(result),
            )
            for ref in refs:
                await conn.execute(
                    "INSERT INTO wms.inventory_identity_movement_links VALUES($1,$2,$3)",
                    application_id,
                    no,
                    ref,
                )
            await conn.execute("SELECT set_config('wms.inventory_identity_action','',true)")

    async def verify_effect(self, conn, document, identity):
        scans = [dict(r) for r in await conn.fetch(sql.SCANS, identity["count_id"])]
        current = await self.capture(conn, document)
        plan = await self.plan(
            conn,
            document,
            current,
            scans,
            await self.config(conn, document["document_id"]),
        )
        if plan["conflicts"] or plan["actions"]:
            raise InventoryDocumentError(
                "Controlled identity correction did not reach counted state",
                code="INVENTORY_IDENTITY_EFFECT_CONFLICT",
                details=plan,
            )
        for item in await conn.fetch(docs.ALL_ITEMS, document["document_id"]):
            physical = sum(
                (
                    Decimal(r["quantity"])
                    for r in current["stocks"]
                    if r["product_id"] == item["product_id"]
                    and r["location_id"] == item["location_id"]
                    and r["status"] == "available"
                    and r["container_code"] is None
                ),
                Decimal(0),
            )
            identified = sum(
                r["lifecycle_status"] == "active"
                and r["product_id"] == item["product_id"]
                and r["location_id"] == item["location_id"]
                and r["container_id"] is None
                for r in current["kiz"]
            )
            if physical - identified != item["actual_quantity"]:
                raise InventoryDocumentError(
                    "Final unidentified quantity differs from count",
                    code="INVENTORY_IDENTITY_EFFECT_CONFLICT",
                )
