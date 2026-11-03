"""Stage 2A fallback: real PG17, no inventory physical writer is enabled."""

import asyncio
import asyncpg
import pytest

from app.shared.config import settings
from test_inventory_documents_postgres import (
    BASE,
    cmd,
    count_item,
    create,
    inventory_env,  # noqa: F401 -- shared isolated fixture
    items,
    physical_state,
)


async def submitted(env, quantity=8):
    document = await create(
        env, mode="partial", locations=[dict(location_id=env["zone"], include_descendants=False)]
    )
    document = await count_item(env, document, (await items(env, document))[0], quantity)
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/submit", json=cmd(document)
    )
    assert response.status_code == 200, response.text
    return response.json()["document"]


def application_command(document, **changes):
    return cmd(document, submitted_revision=document["submitted_revision"], **changes)


async def latest(env, document):
    return (await env["client"].get(f"{BASE}/{document['document_id']}")).json()


@pytest.mark.parametrize("quantity", ["8", "12.25", "10", "0"])
async def test_preparation_is_blocked_for_shortage_surplus_fraction_zero_and_no_delta(
    inventory_env, monkeypatch, quantity
):
    env = inventory_env
    document = await submitted(env, quantity)
    before = await physical_state(env["pool"])
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/application-preview", json=application_command(document)
    )
    assert response.status_code == 200, response.text
    preparation = response.json()
    assert preparation["can_apply"] is False
    assert "physical_count_boundary_unproven" in preparation["blockers"]
    assert len(preparation["components"]) == 1
    assert preparation["components"][0]["stock_evidence"]["physical_quantity"] == "10.00"
    document = await latest(env, document)
    monkeypatch.setattr(settings, "INVENTORY_APPLY_ENABLED", True)
    request = application_command(document)
    for _ in range(2):
        response = await env["client"].post(f"{BASE}/{document['document_id']}/apply", json=request)
        assert response.status_code == 409, response.text
        assert response.json()["error_code"] == "INVENTORY_APPLY_PROTOCOL_UNAVAILABLE"
    assert document == await latest(env, document)
    result = (await env["client"].get(f"{BASE}/{document['document_id']}/application")).json()
    assert result["applied"] is False and result["movements"] == []
    assert result["preparation"] == preparation
    async with env["pool"].acquire() as c:
        assert (
            await c.fetchval(
                "SELECT count(*) FROM wms.inventory_location_claims WHERE document_id=$1",
                document["document_id"],
            )
            == 1
        )
    assert before == await physical_state(env["pool"])


async def test_preparation_replay_and_db_identity_are_not_just_http_key(inventory_env):
    env = inventory_env
    document = await submitted(env)
    request = application_command(document)
    url = f"{BASE}/{document['document_id']}/application-preview"
    responses = await asyncio.gather(*(env["client"].post(url, json=request) for _ in range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    document = await latest(env, document)
    response = await env["client"].post(url, json=application_command(document))
    assert response.status_code == 200 and response.json() == responses[0].json()
    assert document == await latest(env, document)
    changed = dict(request, user_name="different-user")
    conflict = await env["client"].post(url, json=changed)
    assert (
        conflict.status_code == 409
        and conflict.json()["error_code"] == "INVENTORY_IDEMPOTENCY_CONFLICT"
    )
    async with env["pool"].acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_application_preparations") == 1
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_application_components") == 1
        with pytest.raises(asyncpg.UniqueViolationError):
            await c.execute(
                """INSERT INTO wms.inventory_application_preparations
                (document_id,submitted_revision,observed_document_version,created_by,blockers,evidence,lines)
                SELECT document_id,submitted_revision,$1,created_by,blockers,evidence,lines
                FROM wms.inventory_application_preparations""",
                document["version"],
            )


async def test_new_key_concurrent_preparations_share_identity(inventory_env):
    env = inventory_env
    document = await submitted(env)
    responses = await asyncio.gather(
        *(
            env["client"].post(
                f"{BASE}/{document['document_id']}/application-preview",
                json=application_command(document),
            )
            for _ in range(2)
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    document = await latest(env, document)
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/application-preview", json=application_command(document)
    )
    assert response.status_code == 200
    assert response.json()["preparation_id"] == next(
        r.json()["preparation_id"] for r in responses if r.status_code == 200
    )


async def test_stale_revision_and_version_and_wrong_state_fail_without_preparation(
    inventory_env, monkeypatch
):
    env = inventory_env
    document = await submitted(env)
    stale_request = application_command(document)
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/reconcile", json=cmd(document)
    )
    document = response.json()["document"]
    monkeypatch.setattr(settings, "INVENTORY_APPLY_ENABLED", True)
    for suffix in ("application-preview", "apply"):
        url = f"{BASE}/{document['document_id']}/{suffix}"
        response = await env["client"].post(url, json=stale_request)
        assert (
            response.status_code == 409
            and response.json()["error_code"] == "INVENTORY_VERSION_CONFLICT"
        )
        response = await env["client"].post(url, json=application_command(document))
        assert (
            response.status_code == 409
            and response.json()["error_code"] == "INVENTORY_SUBMITTED_REVISION_CONFLICT"
        )
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/return-to-count", json=cmd(document, reason="again")
    )
    document = response.json()
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/application-preview",
        json=cmd(document, submitted_revision=2),
    )
    assert (
        response.status_code == 409 and response.json()["error_code"] == "INVENTORY_STATE_CONFLICT"
    )
    async with env["pool"].acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_application_preparations") == 0


async def test_preparation_rollback_on_last_component_failure(inventory_env):
    env = inventory_env
    document = await create(env)
    for item in await items(env, document):
        document = await count_item(env, document, item, item["initial_expected_quantity"])
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/submit", json=cmd(document)
    )
    document = response.json()["document"]
    async with env["pool"].acquire() as c:
        await c.execute(
            """CREATE FUNCTION public.reject_last_preparation_component() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.item_id=(SELECT max(item_id) FROM wms.inventory_document_items)
          THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='test last component'; END IF;
          RETURN NEW; END $$;
        CREATE TRIGGER test_reject_last_component BEFORE INSERT ON wms.inventory_application_components
        FOR EACH ROW EXECUTE FUNCTION public.reject_last_preparation_component();"""
        )
    # Existing HTTP error mapping returns 500; earlier components/header/audit roll back.
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/application-preview", json=application_command(document)
    )
    assert response.status_code == 500
    async with env["pool"].acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_application_preparations") == 0
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_application_components") == 0
        assert (
            await c.fetchval(
                "SELECT count(*) FROM wms.inventory_document_history WHERE action='prepare_application'"
            )
            == 0
        )
    assert document == await latest(env, document)


async def test_movement_after_count_and_mixed_batch_context_remain_explicit(inventory_env):
    env = inventory_env
    document = await submitted(env)
    async with env["pool"].acquire() as c:
        await c.execute(
            "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity,batch_number) VALUES('receive','inv-a',$1,3,'B')",
            env["zone"],
        )
    before = await physical_state(env["pool"])
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/application-preview", json=application_command(document)
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert {
        "multi_batch_allocation_undefined",
        "state_changed_after_count",
        "movements_since_start_physical_order_unverified",
    } <= set(result["blockers"])
    assert len(result["components"]) == 2
    assert all("allocated_adjustment" not in c for c in result["components"])
    assert before == await physical_state(env["pool"])


async def test_stage1_works_with_apply_flag_true_but_apply_false_flag_rejects(
    inventory_env, monkeypatch
):
    env = inventory_env
    monkeypatch.setattr(settings, "INVENTORY_APPLY_ENABLED", True)
    document = await submitted(env)
    monkeypatch.setattr(settings, "INVENTORY_APPLY_ENABLED", False)
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/apply", json=application_command(document)
    )
    assert (
        response.status_code == 503 and response.json()["error_code"] == "INVENTORY_APPLY_DISABLED"
    )
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/close-without-adjustment",
        json=cmd(document, reason="without changes"),
    )
    assert response.status_code == 200 and response.json()["status"] == "closed_without_adjustment"


async def test_preparation_history_immutable_and_commit_failure_rolls_back(inventory_env):
    env = inventory_env
    document = await submitted(env)
    async with env["pool"].acquire() as c:
        await c.execute(
            """CREATE FUNCTION public.fail_preparation_commit() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='test commit'; END $$;
        CREATE CONSTRAINT TRIGGER test_deferred_preparation AFTER INSERT ON wms.inventory_application_preparations
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.fail_preparation_commit();"""
        )
    request = application_command(document)
    url = f"{BASE}/{document['document_id']}/application-preview"
    response = await env["client"].post(url, json=request)
    assert response.status_code == 500
    assert document == await latest(env, document)
    async with env["pool"].acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM wms.inventory_application_preparations") == 0
        assert (
            await c.fetchval(
                "SELECT count(*) FROM wms.inventory_document_commands WHERE idempotency_key=$1",
                request["idempotency_key"],
            )
            == 0
        )
        await c.execute(
            "DROP TRIGGER test_deferred_preparation ON wms.inventory_application_preparations"
        )
    response = await env["client"].post(url, json=request)
    assert response.status_code == 200
    async with env["pool"].acquire() as c:
        for relation in ("inventory_application_preparations", "inventory_application_components"):
            with pytest.raises(asyncpg.CheckViolationError):
                await c.execute(f"DELETE FROM wms.{relation}")


async def test_delayed_normal_transfer_cannot_be_treated_as_proven_shortage(
    inventory_env, monkeypatch
):
    env = inventory_env
    # Physical transfer of 2 could have occurred before the count, while its normal
    # HTTP recording has not arrived yet. The database cannot observe that fact.
    document = await submitted(env, 8)
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/application-preview", json=application_command(document)
    )
    line = response.json()["lines"][0]
    assert line["conflicts"] == [] and line["movements_since_start"] == []
    assert line["provisional_difference"] == "-2.00"
    monkeypatch.setattr(settings, "INVENTORY_APPLY_ENABLED", True)
    document = await latest(env, document)
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/apply", json=application_command(document)
    )
    assert response.status_code == 409
    async with env["pool"].acquire() as c:
        source = await c.fetchval(
            "SELECT location_code FROM wms.locations WHERE location_id=$1", env["zone"]
        )
        destination = await c.fetchval(
            "SELECT location_code FROM wms.locations WHERE location_id=$1", env["empty"]
        )
    response = await env["client"].post(
        "/api/movements",
        json=[
            dict(
                movement_type="transfer",
                product_id="inv-a",
                from_location_code=source,
                to_location_code=destination,
                quantity=2,
                user_name="delayed-transfer",
            )
        ],
    )
    assert response.status_code == 201, response.text
    async with env["pool"].acquire() as c:
        assert (
            await c.fetchval(
                "SELECT quantity FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1",
                env["zone"],
            )
            == 8
        )
        assert (
            await c.fetchval(
                "SELECT count(*) FROM wms.movements WHERE source_type='inventory_document'"
            )
            == 0
        )


@pytest.mark.parametrize("context", ["active_kiz", "empty_batch", "fbs"])
async def test_unsupported_context_preserved_in_preparation(inventory_env, monkeypatch, context):
    env = inventory_env
    async with env["pool"].acquire() as c:
        if context == "active_kiz":
            async with c.transaction():
                await c.fetch(
                    "SELECT * FROM wms.inventory WHERE product_id='inv-a' AND location_id=$1 FOR UPDATE",
                    env["zone"],
                )
                await c.execute(
                    "INSERT INTO wms.kiz(kiz_code,product_id,location_id,lifecycle_status,created_by) VALUES('PREP-KIZ','inv-a',$1,'active','test')",
                    env["zone"],
                )
            expected_blocker = "active_kiz_physical_separation_required"
        elif context == "empty_batch":
            await c.execute(
                "INSERT INTO wms.movements(movement_type,product_id,to_location_id,quantity,batch_number) VALUES('receive','inv-a',$1,3,'')",
                env["zone"],
            )
            expected_blocker = "null_empty_collision"
        else:
            code = await c.fetchval(
                "SELECT location_code FROM wms.locations WHERE location_id=$1", env["zone"]
            )
            monkeypatch.setattr(settings, "FBS_LOCATION_CODE", code)
            expected_blocker = "configured_fbs_location_physical_boundary_unverified"
    document = await submitted(env)
    before = await physical_state(env["pool"])
    response = await env["client"].post(
        f"{BASE}/{document['document_id']}/application-preview", json=application_command(document)
    )
    assert response.status_code == 200, response.text
    assert expected_blocker in response.json()["blockers"]
    assert before == await physical_state(env["pool"])
