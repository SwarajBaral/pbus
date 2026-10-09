import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import sample

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("PARIBUS_LIVE") != "1", reason="set PARIBUS_LIVE=1"),
]


@pytest.fixture
def live_api():
    with TestClient(create_app(Settings())) as client:
        yield client


def test_end_to_end_against_real_directory(live_api: TestClient) -> None:
    created = live_api.post("/hospitals/bulk", files=sample("valid_5.csv")).json()
    batch = f"/hospitals/bulk/{created['batch_id']}"

    try:
        assert created["status"] == "completed", created
        assert created["processed_hospitals"] == 5
        assert all(h["status"] == "created_and_activated" for h in created["hospitals"])
    finally:
        assert live_api.delete(batch).json()["status"] == "rolled_back"
