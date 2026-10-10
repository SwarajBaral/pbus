import re

import pytest
from fastapi.testclient import TestClient

from tests.conftest import sample, upload
from tests.fake_directory import FakeDirectory

VALID_CSV = "name,address\nGeneral Hospital,1 Main St\nMercy,2 Elm St\n"


def batch_id(html: str) -> str:
    return re.search(r"/dashboard/batches/([0-9a-f-]{36})", html).group(1)


def start(api: TestClient, content: str = VALID_CSV) -> str:
    response = api.post("/dashboard/batches", files=upload(content))
    assert response.status_code == 200
    return batch_id(response.text)


def test_page_renders(api: TestClient) -> None:
    html = api.get("/dashboard").text

    assert "Upload CSV" in html
    assert 'hx-get="/dashboard/batches"' in html
    assert "/static/app.css" in html


def test_static_assets_are_served(api: TestClient) -> None:
    assert api.get("/static/app.css").status_code == 200
    assert api.get("/static/app.js").status_code == 200


def test_validate_shows_row_problems(api: TestClient, directory: FakeDirectory) -> None:
    html = api.post("/dashboard/validate", files=sample("invalid_rows.csv")).text

    assert "CSV has problems" in html
    assert "missing value for: name" in html
    assert directory.hospitals == {}


def test_validate_good_file(api: TestClient) -> None:
    html = api.post("/dashboard/validate", files=sample("valid_5.csv")).text

    assert "Ready to upload 5 hospitals" in html


def test_upload_invalid_file_shows_report(api: TestClient, directory: FakeDirectory) -> None:
    html = api.post("/dashboard/batches", files=sample("too_many_21.csv")).text

    assert "maximum is 20" in html
    assert directory.hospitals == {}


def test_upload_rejects_non_csv(api: TestClient) -> None:
    assert api.post("/dashboard/batches", files=sample("not_a_csv.txt")).status_code == 415


def test_upload_streams_until_complete(api: TestClient) -> None:
    response = api.post("/dashboard/batches", files=upload(VALID_CSV))
    batch = batch_id(response.text)

    assert f'ws-connect="/dashboard/batches/{batch}/ws"' in response.text

    html = api.get(f"/dashboard/batches/{batch}").text
    assert "ws-connect" not in html
    assert "created and activated" in html
    assert "Roll back" in html
    assert "Resume" not in html


def test_websocket_pushes_rendered_job(api: TestClient) -> None:
    batch = start(api)

    with api.websocket_connect(f"/dashboard/batches/{batch}/ws") as ws:
        html = ws.receive_text()

    assert 'id="job-body"' in html
    assert "2/2 created" in html


def test_recent_batches_list(api: TestClient) -> None:
    assert "No batches yet" in api.get("/dashboard/batches").text

    batch = start(api)

    assert batch[:8] in api.get("/dashboard/batches").text


def test_failed_batch_can_be_resumed(api: TestClient, directory: FakeDirectory) -> None:
    directory.rejected_names.add("Mercy")
    batch = start(api)
    assert "Resume" in api.get(f"/dashboard/batches/{batch}").text
    directory.rejected_names.clear()

    assert api.post(f"/dashboard/batches/{batch}/resume").status_code == 200

    assert "completed" in api.get(f"/dashboard/batches/{batch}").text
    assert len(directory.batch(batch)) == 2


def test_rollback(api: TestClient, directory: FakeDirectory) -> None:
    batch = start(api)

    html = api.post(f"/dashboard/batches/{batch}/rollback").text

    assert "rolled back" in html
    assert directory.hospitals == {}


@pytest.mark.parametrize("action", ["resume", "rollback"])
def test_actions_conflict_when_not_allowed(api: TestClient, action: str) -> None:
    batch = start(api)
    api.post(f"/dashboard/batches/{batch}/rollback")

    assert api.post(f"/dashboard/batches/{batch}/{action}").status_code == 409


def test_unknown_batch(api: TestClient) -> None:
    assert api.get("/dashboard/batches/00000000-0000-0000-0000-000000000000").status_code == 404
