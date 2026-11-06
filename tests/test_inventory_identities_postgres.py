"""Stage 2B HTTP and real controlled effects, only on disposable PostgreSQL."""
import asyncio
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from test_inventory_documents_postgres import (
    BASE,
    cmd,
    create,
    items,
    count_item,
    physical_state,
    inventory_env,  # noqa: F401
)  # noqa: F401
from test_inventory_freeze_postgres import begin, confirm, freeze_env, fbs_http  # noqa: F401
from test_inventory_physical_apply_postgres import (
    physical_env,  # noqa: F401
    approval,
    submit_preview,
)  # noqa: F401

from test_inventory_deployment_postgres import (
    runtime_env,  # noqa: F401,F811
    migration_env,  # noqa: F401
    legacy_template,  # noqa: F401
)  # noqa: F401,E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
async def identity_env(physical_env):  # noqa: F811
    env = physical_env
    async with env["pool"].acquire() as c:
        if not await c.fetchval("SELECT to_regclass('wms.inventory_identity_config') IS NOT NULL"):
            await c.execute(
                (ROOT / "scripts/migrations/20261105_add_inventory_identity_counts.sql").read_text()
            )
        await c.execute(
            "INSERT INTO wms.kiz(kiz_code,product_id,location_id,lifecycle_status,created_by) VALUES('IDENTITY-A','inv-a',$1,'active','test')",
            env["zone"],
        )
    return env


async def action(env, doc, path, **payload):
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/{path}", json=cmd(doc, **payload)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def document(env, containers=False):
    doc = await create(
        env,
        mode="partial",
        start=False,
        locations=[
            dict(location_id=env["zone"], include_descendants=False),
            dict(location_id=env["empty"], include_descendants=False),
        ],
    )
    doc = await action(env, doc, "identity-accounting", containers=containers)
    doc = await action(env, doc, "start")
    doc, _ = await begin(env, doc)
    doc, _ = await confirm(env, doc)
    return doc


async def scan_count(env, doc, scans):
    result = await action(env, doc, "identity-counts")
    doc = result["document"]
    count = result["count_id"]
    for scan in scans:
        result = await action(env, doc, f"identity-counts/{count}/scans", **scan)
        doc = result["document"]
    result = await action(env, doc, f"identity-counts/{count}/complete")
    return result["document"], result["result"], count


async def loose_counts(env, doc, quantities=None):
    for item in await items(env, doc):
        qty = (quantities or {}).get(
            (item["product_id"], item["location_id"]), item["initial_expected_quantity"]
        )
        doc = await count_item(env, doc, item, qty)
    return doc


def kiz_scan(env, holder=None, location="zone", code="IDENTITY-A"):
    return dict(kind="kiz", kiz_code=code, location_id=env[location], holder_qr=holder)


async def apply(env, doc):
    doc, prep = await submit_preview(env, doc)
    assert prep["can_apply"], prep
    doc = (await action(env, doc, "freeze/ready", submitted_revision=doc["submitted_revision"]))[
        "document"
    ]
    request = approval(doc)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=request)
    assert response.status_code == 200, response.text
    return response.json(), request


async def container(env, qty="2", codes=None, parent=None, qr="IDENTITY-C"):
    async with env["pool"].acquire() as c:
        cid = await c.fetchval(
            "INSERT INTO wms.containers(qr_code,container_type,location_id,parent_container_id,status) VALUES($1,'box',$2,$3,'empty') RETURNING container_id",
            qr,
            env["zone"],
            parent,
        )
    if Decimal(qty):
        response = await env["client"].post(
            "/api/container-operations/fill",
            json=dict(
                source_system="test",
                external_operation_id=qr,
                author="test",
                container_id=cid,
                items=[
                    dict(
                        external_line_id="1",
                        product_id="inv-a",
                        quantity=qty,
                        kiz_codes=codes or [],
                    )
                ],
            ),
        )
        assert response.status_code == 201, response.text
    return cid


def container_scan(env, qty, location="zone", qr="IDENTITY-C"):
    return dict(
        kind="container",
        container_qr=qr,
        location_id=env[location],
        contents=[dict(product_id="inv-a", quantity=qty)] if Decimal(qty) else [],
    )


async def test_kiz_misplacement_atomic_transfer_and_replay(identity_env):
    env = identity_env
    doc = await document(env)
    doc, result, _ = await scan_count(env, doc, [kiz_scan(env, location="empty")])
    assert not result["conflicts"] and result["actions"][0]["kind"] == "kiz_transfer"
    doc = await loose_counts(env, doc)
    result, request = await apply(env, doc)
    assert result["identity_actions"] and result["movements"][0]["source_type"] == "kiz_operation"
    again = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=request)
    assert again.json() == result
    async with env["pool"].acquire() as c:
        assert (
            await c.fetchval("SELECT location_id FROM wms.kiz WHERE kiz_code='IDENTITY-A'")
            == env["empty"]
        )
        assert (
            await c.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 9
        )
        assert (
            await c.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["empty"],
            )
            == 1
        )
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_identity_movement_links") == 1
        assert await c.fetchval("SELECT count(*) FROM wms.check_kiz_holder_integrity()") == 0


@pytest.mark.parametrize(
    "kind", ["missing", "unknown", "duplicate", "wrong_holder", "registered", "deactivated"]
)
async def test_ambiguous_identity_is_not_applied(identity_env, kind):
    env = identity_env
    if kind in ("registered", "deactivated"):
        async with env["pool"].acquire() as c:
            await c.execute(
                "INSERT INTO wms.kiz(kiz_code,product_id,lifecycle_status,created_by,closed_at) VALUES('OTHER','inv-a',$1::varchar,'test',CASE WHEN $1::varchar='deactivated' THEN now() END)",
                kind,
            )
    doc = await document(env, containers=kind == "wrong_holder")
    scans = [] if kind == "missing" else [kiz_scan(env)]
    if kind == "unknown":
        scans.append(kiz_scan(env, code="UNKNOWN"))
    if kind == "duplicate":
        scans.append(kiz_scan(env))
    if kind == "wrong_holder":
        scans = [kiz_scan(env, holder="UNKNOWN")]
    if kind in ("registered", "deactivated"):
        scans.append(kiz_scan(env, code="OTHER"))
    before = await physical_state(env["pool"])
    doc, plan, _ = await scan_count(env, doc, scans)
    assert plan["conflicts"] and plan["resolution"]
    doc = await loose_counts(env, doc)
    doc, prep = await submit_preview(env, doc)
    assert not prep["can_apply"]
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 409
    assert before == await physical_state(env["pool"])


async def test_count_history_normalization_duplicates_and_idempotency(identity_env):
    env = identity_env
    doc = await document(env)
    first = await action(env, doc, "identity-counts")
    doc = first["document"]
    cid = first["count_id"]
    payload = cmd(doc, **kiz_scan(env, code="A" * 24 + "suffix"))
    url = f"{BASE}/{doc['document_id']}/identity-counts/{cid}/scans"
    response = await env["client"].post(url, json=payload)
    assert response.status_code == 200, response.text
    assert (await env["client"].post(url, json=payload)).json() == response.json()
    doc = response.json()["document"]
    result = await action(env, doc, f"identity-counts/{cid}/complete")
    doc = result["document"]
    assert (await env["client"].post(url, json=cmd(doc, **kiz_scan(env)))).status_code == 409
    doc, _, _ = await scan_count(env, doc, [kiz_scan(env)])
    history = (await env["client"].get(f"{BASE}/{doc['document_id']}/identities")).json()
    assert history["total"] == 2
    assert history["attempts"][1]["scans"][0]["code"] == "A" * 24
    assert history["snapshot"]["kiz"][0]["kiz_code"] == "IDENTITY-A"


@pytest.mark.parametrize("method", ["container_move", "fill", "extract"])
async def test_container_controlled_physical_effects(identity_env, method):
    env = identity_env
    await container(env, qty="2", codes=["IDENTITY-A"] if method != "fill" else [])
    doc = await document(env, containers=True)
    qty = {"container_move": "2", "fill": "3", "extract": "1"}[method]
    loc = "empty" if method == "container_move" else "zone"
    holder = None if method == "extract" else "IDENTITY-C"
    doc, plan, _ = await scan_count(
        env,
        doc,
        [
            container_scan(env, qty, location=loc),
            kiz_scan(env, holder=holder, location=loc if holder else "zone"),
        ],
    )
    assert not plan["conflicts"], plan
    assert plan["actions"][0]["kind"] == method
    # Observed unidentified is distinct from both contained units and identified units.
    loose = {"container_move": "8", "fill": "7", "extract": "8"}[method]
    doc = await loose_counts(env, doc, {("inv-a", env["zone"]): loose})
    result, _ = await apply(env, doc)
    async with env["pool"].acquire() as c:
        assert (
            await c.fetchval(
                "SELECT sum(quantity) FROM wms.inventory WHERE product_id='inv-a' AND location_id=ANY($1::bigint[])",
                [env["zone"], env["empty"]],
            )
            == 10
        )
        assert await c.fetchval(
            "SELECT quantity FROM wms.container_contents WHERE status='active' AND product_id='inv-a'"
        ) == Decimal(qty)
        assert await c.fetchval("SELECT count(*) FROM wms.check_kiz_holder_integrity()") == 0
    assert len(result["movements"]) == (1 if method == "container_move" else 2)


async def test_mixed_loose_adjust_kiz_and_container_no_double_count(identity_env):
    env = identity_env
    await container(env, qty="2")
    async with env["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-b',$1,5)",
            env["zone"],
        )
    doc = await document(env, containers=True)
    doc, plan, _ = await scan_count(
        env, doc, [container_scan(env, "3"), kiz_scan(env, holder="IDENTITY-C")]
    )
    assert not plan["conflicts"], plan
    doc = await loose_counts(env, doc, {("inv-a", env["zone"]): "7", ("inv-b", env["zone"]): "4"})
    result, _ = await apply(env, doc)
    assert len(result["movements"]) == 3
    async with env["pool"].acquire() as c:
        assert (
            await c.fetchval(
                "SELECT sum(quantity) FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 10
        )
        assert (
            await c.fetchval(
                "SELECT sum(quantity) FROM wms.inventory WHERE product_id='inv-b' AND location_id=$1",
                env["zone"],
            )
            == 4
        )


async def test_last_movement_error_rolls_back_controlled_operations(identity_env):
    env = identity_env
    async with env["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity) VALUES('receive','inv-b',$1,5)",
            env["zone"],
        )
    doc = await document(env)
    doc, _, _ = await scan_count(env, doc, [kiz_scan(env, location="empty")])
    doc = await loose_counts(env, doc, {("inv-b", env["zone"]): "4"})
    doc, prep = await submit_preview(env, doc)
    assert prep["can_apply"], prep
    doc = (await action(env, doc, "freeze/ready", submitted_revision=doc["submitted_revision"]))[
        "document"
    ]
    async with env["pool"].acquire() as c:
        await c.execute(
            """CREATE FUNCTION wms.fail_identity_tail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
         IF NEW.source_type='inventory_document' THEN RAISE EXCEPTION USING ERRCODE='P7603',MESSAGE='tail failure'; END IF; RETURN NEW; END $$;
         CREATE CONSTRAINT TRIGGER test_identity_tail AFTER INSERT ON wms.movements DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION wms.fail_identity_tail();"""
        )
    before = await physical_state(env["pool"])
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 409, response.text
    assert before == await physical_state(env["pool"])
    async with env["pool"].acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_identity_actions") == 0
        assert await c.fetchval("SELECT count(*) FROM wms.kiz_operations") == 0
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_applications") == 0


async def test_nested_containers_rejected_by_current_db_contract(identity_env):
    env = identity_env
    cid = await container(env, qty="0")
    with pytest.raises(asyncpg.CheckViolationError) as error:
        await container(env, qty="0", parent=cid, qr="CHILD")
    assert error.value.constraint_name == "chk_container_flat"
    doc = await document(env, containers=True)
    doc, plan, _ = await scan_count(env, doc, [container_scan(env, "0"), kiz_scan(env)])
    assert not plan["conflicts"], plan
    doc = await loose_counts(env, doc)
    result, _ = await apply(env, doc)
    assert result["movements"] == []


async def test_fbs_invalidation_prevents_identity_apply(identity_env, monkeypatch):
    env = identity_env
    doc = await document(env)
    doc, _, _ = await scan_count(env, doc, [kiz_scan(env)])
    doc = await loose_counts(env, doc)
    response = await fbs_http(env, monkeypatch)
    assert response.status_code == 201, response.text
    doc, prep = await submit_preview(env, doc)
    assert not prep["can_apply"]


@pytest.mark.parametrize(
    "kind", ["missing_container", "unknown_container", "duplicate_container", "nested_move"]
)
async def test_container_conflicts_require_resolution(identity_env, kind):
    env = identity_env
    await container(env, qty="0")
    doc = await document(env, containers=True)
    scans = [kiz_scan(env)]
    if kind != "missing_container":
        scans.append(
            container_scan(
                env,
                "0",
                qr="UNKNOWN" if kind == "unknown_container" else "IDENTITY-C",
                location="empty" if kind == "nested_move" else "zone",
            )
        )
    if kind == "duplicate_container":
        scans.append(container_scan(env, "0"))
    if kind == "nested_move":
        scans.append(container_scan(env, "0", qr="CHILD", location="empty"))
    doc, result, _ = await scan_count(env, doc, scans)
    assert result["conflicts"]


async def test_concurrent_identity_scan_version_serialization(identity_env):
    env = identity_env
    doc = await document(env)
    r = await action(env, doc, "identity-counts")
    doc = r["document"]
    cid = r["count_id"]
    url = f"{BASE}/{doc['document_id']}/identity-counts/{cid}/scans"
    responses = await asyncio.gather(
        *(env["client"].post(url, json=cmd(doc, **kiz_scan(env))) for _ in range(2))
    )
    assert sorted(r.status_code for r in responses) == [200, 409]


async def test_identity_snapshot_and_finished_history_immutable(identity_env):
    env = identity_env
    doc = await document(env)
    doc, _, cid = await scan_count(env, doc, [kiz_scan(env)])
    async with env["pool"].acquire() as c:
        with pytest.raises(asyncpg.PostgresError):
            await c.execute(
                "UPDATE wms.inventory_identity_counts SET author=$1 WHERE count_id=$2",
                "rewrite",
                cid,
            )
        with pytest.raises(asyncpg.PostgresError):
            await c.execute(
                "UPDATE wms.inventory_identity_snapshots SET evidence='{}' WHERE document_id=$1",
                doc["document_id"],
            )


@pytest.mark.parametrize("operation", ["kiz_transfer", "container_move", "fill", "extract"])
async def test_identity_apply_under_nonowner_scoped_runtime(
    identity_env, runtime_env, operation  # noqa: F811
):  # noqa: F811
    env = identity_env
    if operation == "kiz_transfer":
        doc = await document(env)
        doc, _, _ = await scan_count(env, doc, [kiz_scan(env, location="empty")])
        doc = await loose_counts(env, doc)
    else:
        await container(env, qty="2", codes=["IDENTITY-A"] if operation != "fill" else [])
        doc = await document(env, containers=True)
        qty = {"container_move": "2", "fill": "3", "extract": "1"}[operation]
        location = "empty" if operation == "container_move" else "zone"
        holder = None if operation == "extract" else "IDENTITY-C"
        doc, plan, _ = await scan_count(
            env,
            doc,
            [
                container_scan(env, qty, location=location),
                kiz_scan(env, holder=holder, location=location if holder else "zone"),
            ],
        )
        assert not plan["conflicts"], plan
        doc = await loose_counts(
            env, doc, {("inv-a", env["zone"]): "7" if operation == "fill" else "8"}
        )
    result, _ = await apply(env, doc)
    assert result["applied"]
    async with runtime_env["pool"].acquire() as c:
        assert not await c.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
        assert not await c.fetchval(
            "SELECT has_table_privilege(current_user,'wms.inventory_identity_snapshots','UPDATE')"
        )
        assert not await c.fetchval(
            "SELECT has_function_privilege(current_user,'wms.inventory_identity_runtime_privileges()','EXECUTE')"
        )


async def test_new_freeze_epoch_and_return_require_new_identity_count(identity_env):
    env = identity_env
    doc = await document(env)
    doc, _, _ = await scan_count(env, doc, [kiz_scan(env)])
    doc = await loose_counts(env, doc)
    doc, prep = await submit_preview(env, doc)
    assert prep["can_apply"]
    doc = await action(env, doc, "return-to-count", reason="Repeat physical identity count")
    doc = await loose_counts(env, doc)
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/submit", json=cmd(doc))
    assert response.status_code == 409
    doc, _, _ = await scan_count(env, doc, [kiz_scan(env)])
    doc, prep = await submit_preview(env, doc)
    assert prep["can_apply"]


async def test_fbs_during_identity_count_marks_attempt_stale(identity_env, monkeypatch):
    env = identity_env
    doc = await document(env)
    r = await action(env, doc, "identity-counts")
    doc = r["document"]
    cid = r["count_id"]
    doc = (await action(env, doc, f"identity-counts/{cid}/scans", **kiz_scan(env)))["document"]
    response = await fbs_http(env, monkeypatch)
    assert response.status_code == 201, response.text
    result = await action(env, doc, f"identity-counts/{cid}/complete")
    assert "state_changed_during_identity_count" in [
        r["reason"] for r in result["result"]["conflicts"]
    ]


async def test_scans_outside_scope_and_bigint_rejected(identity_env):
    env = identity_env
    doc = await document(env)
    result = await action(env, doc, "identity-counts")
    doc = result["document"]
    cid = result["count_id"]
    url = f"{BASE}/{doc['document_id']}/identity-counts/{cid}/scans"
    response = await env["client"].post(url, json=cmd(doc, **kiz_scan(env, location="bin")))
    assert response.status_code == 409
    response = await env["client"].post(
        url, json=cmd(doc, kind="kiz", kiz_code="IDENTITY-A", location_id=2**63)
    )
    assert response.status_code == 422
    response = await env["client"].post(
        f"{BASE}/{doc['document_id']}/identity-counts/{2**63}/complete", json=cmd(doc)
    )
    assert response.status_code == 422


async def test_apply_disabled_leaves_identities_unchanged(identity_env, monkeypatch):
    from app.shared.config import settings

    env = identity_env
    doc = await document(env)
    doc, _, _ = await scan_count(env, doc, [kiz_scan(env, location="empty")])
    doc = await loose_counts(env, doc)
    doc, prep = await submit_preview(env, doc)
    assert prep["can_apply"]
    doc = (await action(env, doc, "freeze/ready", submitted_revision=doc["submitted_revision"]))[
        "document"
    ]
    monkeypatch.setattr(settings, "INVENTORY_APPLY_ENABLED", False)
    before = await physical_state(env["pool"])
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 503
    assert before == await physical_state(env["pool"])


async def test_concurrent_identity_apply_same_key_once(identity_env):
    env = identity_env
    doc = await document(env)
    doc, _, _ = await scan_count(env, doc, [kiz_scan(env, location="empty")])
    doc = await loose_counts(env, doc)
    doc, prep = await submit_preview(env, doc)
    assert prep["can_apply"]
    doc = (await action(env, doc, "freeze/ready", submitted_revision=doc["submitted_revision"]))[
        "document"
    ]
    request = approval(doc)
    url = f"{BASE}/{doc['document_id']}/apply"
    responses = await asyncio.gather(*(env["client"].post(url, json=request) for _ in range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    async with env["pool"].acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_applications") == 1
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_identity_actions") == 1


async def test_unsupported_admission_definition_rolls_back_migration(migration_env):  # noqa: F811
    async with migration_env["pool"].acquire() as c:
        for name in [
            "20261030_add_inventory_location_freeze.sql",
            "20261030_add_inventory_physical_apply.sql",
        ]:
            await c.execute((ROOT / "scripts/migrations" / name).read_text())
        signature = "wms.inventory_check_location_write(bigint,character varying,boolean,boolean,bigint,timestamp with time zone)"
        definition = await c.fetchval("SELECT pg_get_functiondef($1::regprocedure)", signature)
        await c.execute(definition.replace("NOWAIT", "NOWAIT /* unsupported drift */", 1))
        before = await c.fetchval("SELECT pg_get_functiondef($1::regprocedure)", signature)
        with pytest.raises(asyncpg.PostgresError, match="Unsupported Stage 2A admission"):
            await c.execute(
                (ROOT / "scripts/migrations/20261105_add_inventory_identity_counts.sql").read_text()
            )
        await c.execute("ROLLBACK")
        assert await c.fetchval("SELECT to_regclass('wms.inventory_identity_config')") is None
        assert await c.fetchval("SELECT pg_get_functiondef($1::regprocedure)", signature) == before


async def test_unexpected_projection_effect_rolls_back_identity_apply(identity_env):
    env = identity_env
    doc = await document(env)
    doc, _, _ = await scan_count(env, doc, [kiz_scan(env, location="empty")])
    doc = await loose_counts(env, doc)
    doc, prep = await submit_preview(env, doc)
    assert prep["can_apply"]
    doc = (await action(env, doc, "freeze/ready", submitted_revision=doc["submitted_revision"]))[
        "document"
    ]
    async with env["pool"].acquire() as c:
        await c.execute(
            """CREATE FUNCTION wms.bad_identity_projection() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
         IF NEW.source_type='kiz_operation' THEN UPDATE wms.inventory SET quantity=quantity+1
          WHERE product_id=NEW.product_id AND location_id=NEW.from_location_id AND container_code IS NULL; END IF; RETURN NEW; END $$;
         CREATE TRIGGER zz_bad_identity_projection AFTER INSERT ON wms.movements FOR EACH ROW EXECUTE FUNCTION wms.bad_identity_projection();"""
        )
    before = await physical_state(env["pool"])
    response = await env["client"].post(f"{BASE}/{doc['document_id']}/apply", json=approval(doc))
    assert response.status_code == 409, response.text
    assert before == await physical_state(env["pool"])


async def test_new_attempt_supersedes_open_one_without_losing_scans(identity_env):
    env = identity_env
    doc = await document(env)
    first = await action(env, doc, "identity-counts")
    doc = first["document"]
    cid = first["count_id"]
    doc = (await action(env, doc, f"identity-counts/{cid}/scans", **kiz_scan(env)))["document"]
    second = await action(env, doc, "identity-counts")
    doc = second["document"]
    history = (await env["client"].get(f"{BASE}/{doc['document_id']}/identities")).json()
    assert history["attempts"][1]["status"] == "superseded"
    assert history["attempts"][1]["scans"][0]["code"] == "IDENTITY-A"


async def test_identity_postinstall_preflight_is_read_only(identity_env):
    async with identity_env["pool"].acquire() as c:
        before = await c.fetchval("SELECT count(*) FROM wms.inventory_identity_config")
        await c.execute(
            (
                ROOT / "scripts/migrations/20261105_inventory_identity_privileges_preflight.sql"
            ).read_text()
        )
        assert before == await c.fetchval("SELECT count(*) FROM wms.inventory_identity_config")
        assert not c.is_in_transaction()


async def test_identity_apply_does_not_upgrade_behind_fbs_location_share(identity_env):
    env = identity_env
    doc = await document(env)
    doc, _, _ = await scan_count(env, doc, [kiz_scan(env, location="empty")])
    doc = await loose_counts(env, doc)
    doc, prep = await submit_preview(env, doc)
    assert prep["can_apply"]
    doc = (await action(env, doc, "freeze/ready", submitted_revision=doc["submitted_revision"]))[
        "document"
    ]
    request = approval(doc)
    url = f"{BASE}/{doc['document_id']}/apply"
    before = await physical_state(env["pool"])
    async with env["pool"].acquire() as c:
        async with c.transaction():
            # Same row lock as task-level FBS source lookup. It must never be
            # waited for while holding the freeze fence in the reverse order.
            await c.fetchrow(
                "SELECT location_id FROM wms.locations WHERE location_id=$1 FOR SHARE", env["zone"]
            )
            response = await asyncio.wait_for(env["client"].post(url, json=request), timeout=2)
            assert response.status_code == 409, response.text
            assert response.json()["error_code"] == "INVENTORY_CONCURRENT_COMMAND"
    assert before == await physical_state(env["pool"])
    response = await env["client"].post(url, json=request)
    assert response.status_code == 200, response.text
