import pytest
from fastapi.testclient import TestClient

from tests.conftest import sample, upload
from tests.fake_directory import FakeDirectory

VALID_CSV = "name,address,phone\nGeneral Hospital,1 Main St,555-1234\nMercy,2 Elm St,\n"


def bulk(api: TestClient, files: dict, **params) -> dict:
    response = api.post("/hospitals/bulk", files=files, params=params)
    return {"status_code": response.status_code, **response.json()}


def test_health(api: TestClient) -> None:
    assert api.get("/health").json() == {"status": "ok"}


def test_root_redirects_to_docs(api: TestClient) -> None:
    assert api.get("/", follow_redirects=False).headers["location"] == "/docs"


def test_bulk_create_returns_full_result(api: TestClient, directory: FakeDirectory) -> None:
    body = bulk(api, upload(VALID_CSV))

    assert body["status_code"] == 200
    assert body["status"] == "completed"
    assert body["batch_activated"] is True
    assert (body["total_hospitals"], body["processed_hospitals"], body["failed_hospitals"]) == (
        2,
        2,
        0,
    )
    assert body["processing_time_seconds"] >= 0
    assert body["hospitals"][0] == {
        "row": 1,
        "hospital_id": 1,
        "name": "General Hospital",
        "address": "1 Main St",
        "phone": "555-1234",
        "status": "created_and_activated",
        "error": None,
    }
    assert len(directory.batch(body["batch_id"])) == 2


def test_bulk_create_partial_failure(api: TestClient, directory: FakeDirectory) -> None:
    directory.rejected_names.add("Mercy")

    body = bulk(api, upload(VALID_CSV))

    assert body["status"] == "failed"
    assert body["batch_activated"] is False
    assert (body["processed_hospitals"], body["failed_hospitals"]) == (1, 1)
    assert body["hospitals"][1]["status"] == "failed"


def test_invalid_csv_is_rejected_before_any_upstream_call(
    api: TestClient, directory: FakeDirectory
) -> None:
    body = bulk(api, sample("invalid_rows.csv"))

    assert body["status_code"] == 422
    assert [e["row"] for e in body["detail"]["errors"]] == [2, 3, 4]
    assert directory.hospitals == {}


@pytest.mark.parametrize(
    ("filename", "status_code"),
    [
        ("too_many_21.csv", 422),
        ("missing_address_column.csv", 422),
        ("unknown_column.csv", 422),
        ("header_only.csv", 422),
        ("empty.csv", 422),
        ("not_a_csv.txt", 415),
    ],
)
def test_bad_samples_are_rejected(
    api: TestClient, directory: FakeDirectory, filename: str, status_code: int
) -> None:
    assert bulk(api, sample(filename))["status_code"] == status_code
    assert directory.hospitals == {}


@pytest.mark.parametrize(
    ("filename", "count"),
    [
        ("valid_5.csv", 5),
        ("max_20.csv", 20),
        ("messy_but_valid.csv", 2),
        ("no_phone_column.csv", 2),
    ],
)
def test_good_samples_complete(api: TestClient, filename: str, count: int) -> None:
    body = bulk(api, sample(filename))

    assert body["status"] == "completed"
    assert body["processed_hospitals"] == count


def test_oversized_upload_is_rejected(api: TestClient) -> None:
    content = "name,address\n" + "x" * 70_000

    assert bulk(api, upload(content))["status_code"] == 413


def test_missing_file_is_rejected(api: TestClient) -> None:
    assert api.post("/hospitals/bulk").status_code == 422


def test_async_mode_returns_202_then_poll(api: TestClient) -> None:
    accepted = bulk(api, upload(VALID_CSV), wait=False)

    assert accepted["status_code"] == 202
    assert accepted["status"] == "processing"
    assert accepted["processed_hospitals"] == 0

    polled = api.get(f"/hospitals/bulk/{accepted['batch_id']}").json()
    assert polled["status"] == "completed"
    assert polled["processed_hospitals"] == 2


def test_get_unknown_batch(api: TestClient) -> None:
    response = api.get("/hospitals/bulk/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_get_rejects_malformed_batch_id(api: TestClient) -> None:
    assert api.get("/hospitals/bulk/not-a-uuid").status_code == 422


def test_resume_flow(api: TestClient, directory: FakeDirectory) -> None:
    directory.rejected_names.add("Mercy")
    failed = bulk(api, upload(VALID_CSV))
    directory.rejected_names.clear()

    resumed = api.post(f"/hospitals/bulk/{failed['batch_id']}/resume").json()

    assert resumed["status"] == "completed"
    assert resumed["batch_activated"] is True
    assert len(directory.batch(failed["batch_id"])) == 2


def test_resume_in_async_mode(api: TestClient, directory: FakeDirectory) -> None:
    directory.activation_failures = [500]
    failed = bulk(api, upload(VALID_CSV))

    response = api.post(f"/hospitals/bulk/{failed['batch_id']}/resume", params={"wait": False})

    assert response.status_code == 202
    assert api.get(f"/hospitals/bulk/{failed['batch_id']}").json()["status"] == "completed"


def test_resume_completed_batch_conflicts(api: TestClient) -> None:
    done = bulk(api, upload(VALID_CSV))

    response = api.post(f"/hospitals/bulk/{done['batch_id']}/resume")

    assert response.status_code == 409
    assert "is completed" in response.json()["detail"]


def test_rollback_failed_batch(api: TestClient, directory: FakeDirectory) -> None:
    directory.rejected_names.add("Mercy")
    failed = bulk(api, upload(VALID_CSV))

    body = api.delete(f"/hospitals/bulk/{failed['batch_id']}").json()

    assert body["status"] == "rolled_back"
    assert {h["status"] for h in body["hospitals"]} == {"rolled_back"}
    assert directory.hospitals == {}


def test_rollback_completed_batch(api: TestClient, directory: FakeDirectory) -> None:
    done = bulk(api, upload(VALID_CSV))

    assert api.delete(f"/hospitals/bulk/{done['batch_id']}").json()["status"] == "rolled_back"
    assert directory.hospitals == {}


def test_rolled_back_batch_is_final(api: TestClient) -> None:
    done = bulk(api, upload(VALID_CSV))
    batch = f"/hospitals/bulk/{done['batch_id']}"
    api.delete(batch)

    assert api.post(f"{batch}/resume").status_code == 409
    assert api.delete(batch).status_code == 409


def test_validate_reports_without_processing(api: TestClient, directory: FakeDirectory) -> None:
    good = api.post("/hospitals/bulk/validate", files=sample("valid_5.csv")).json()
    bad = api.post("/hospitals/bulk/validate", files=sample("invalid_rows.csv")).json()

    assert (good["valid"], good["total_rows"], good["errors"]) == (True, 5, [])
    assert bad["valid"] is False
    assert [r["row"] for r in bad["rows"]] == [1]
    assert len(bad["errors"]) == 3
    assert directory.hospitals == {}


def test_validate_reports_file_level_errors(api: TestClient) -> None:
    body = api.post("/hospitals/bulk/validate", files=sample("too_many_21.csv")).json()

    assert body["errors"] == [{"row": None, "message": "CSV contains 21 hospitals; maximum is 20"}]
