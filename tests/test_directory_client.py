import json
from uuid import uuid4

import httpx
import pytest
import respx

from app.directory_client import DirectoryClient, DirectoryError
from app.schemas import HospitalRow

ROW = HospitalRow(row=1, name="General", address="1 Main St")
CREATED = {"id": 7, "name": "General", "address": "1 Main St", "phone": None}


async def test_create_sends_batch_id_and_returns_id(
    client: DirectoryClient, router: respx.Router
) -> None:
    batch_id = uuid4()
    route = router.post("/hospitals/").respond(200, json=CREATED)

    assert await client.create_hospital(ROW, batch_id) == 7
    assert json.loads(route.calls.last.request.content) == {
        "name": "General",
        "address": "1 Main St",
        "phone": None,
        "creation_batch_id": str(batch_id),
    }


async def test_create_retries_when_connection_fails(
    client: DirectoryClient, router: respx.Router
) -> None:
    route = router.post("/hospitals/").mock(
        side_effect=[httpx.ConnectError("down"), httpx.Response(200, json=CREATED)]
    )

    assert await client.create_hospital(ROW, uuid4()) == 7
    assert route.call_count == 2


async def test_create_does_not_retry_read_timeout(
    client: DirectoryClient, router: respx.Router
) -> None:
    route = router.post("/hospitals/").mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(DirectoryError, match="ReadTimeout"):
        await client.create_hospital(ROW, uuid4())
    assert route.call_count == 1


@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_retries_transient_statuses(
    client: DirectoryClient, router: respx.Router, status: int
) -> None:
    route = router.post("/hospitals/").mock(
        side_effect=[httpx.Response(status), httpx.Response(200, json=CREATED)]
    )

    assert await client.create_hospital(ROW, uuid4()) == 7
    assert route.call_count == 2


async def test_gives_up_after_max_attempts(client: DirectoryClient, router: respx.Router) -> None:
    route = router.post("/hospitals/").respond(503)

    with pytest.raises(DirectoryError, match="returned 503"):
        await client.create_hospital(ROW, uuid4())
    assert route.call_count == 3


async def test_client_errors_fail_fast_with_detail(
    client: DirectoryClient, router: respx.Router
) -> None:
    route = router.post("/hospitals/").respond(422, json={"detail": "name too short"})

    with pytest.raises(DirectoryError, match=r"422.*name too short"):
        await client.create_hospital(ROW, uuid4())
    assert route.call_count == 1


async def test_rejects_unexpected_payload(client: DirectoryClient, router: respx.Router) -> None:
    router.post("/hospitals/").respond(200, text="<html>oops</html>")

    with pytest.raises(DirectoryError, match="invalid JSON"):
        await client.create_hospital(ROW, uuid4())


async def test_rejects_payload_without_id(client: DirectoryClient, router: respx.Router) -> None:
    router.post("/hospitals/").respond(200, json={"name": "General"})

    with pytest.raises(DirectoryError, match="unexpected hospital payload"):
        await client.create_hospital(ROW, uuid4())


async def test_idempotent_calls_retry_timeouts(
    client: DirectoryClient, router: respx.Router
) -> None:
    batch_id = uuid4()
    route = router.patch(f"/hospitals/batch/{batch_id}/activate").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json={})]
    )

    await client.activate_batch(batch_id)
    assert route.call_count == 2


async def test_activate_missing_batch_is_an_error(
    client: DirectoryClient, router: respx.Router
) -> None:
    batch_id = uuid4()
    router.patch(f"/hospitals/batch/{batch_id}/activate").respond(404)

    with pytest.raises(DirectoryError, match="404"):
        await client.activate_batch(batch_id)


async def test_list_batch_parses_hospitals(client: DirectoryClient, router: respx.Router) -> None:
    batch_id = uuid4()
    router.get(f"/hospitals/batch/{batch_id}").respond(200, json=[CREATED])

    [hospital] = await client.list_batch(batch_id)
    assert hospital.id == 7
    assert hospital.matches(ROW)


async def test_list_missing_batch_is_empty(client: DirectoryClient, router: respx.Router) -> None:
    batch_id = uuid4()
    router.get(f"/hospitals/batch/{batch_id}").respond(404)

    assert await client.list_batch(batch_id) == []


async def test_delete_missing_batch_is_noop(client: DirectoryClient, router: respx.Router) -> None:
    batch_id = uuid4()
    route = router.delete(f"/hospitals/batch/{batch_id}").respond(404)

    await client.delete_batch(batch_id)
    assert route.called
