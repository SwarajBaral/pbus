from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import Settings
from app.directory_client import DirectoryClient
from app.main import create_app
from app.processor import BulkProcessor
from app.schemas import BulkJob, HospitalRow
from tests.fake_directory import FakeDirectory

DIRECTORY_URL = "http://directory.test"
SAMPLES = Path(__file__).parent.parent / "samples"


@pytest.fixture
def settings() -> Settings:
    return Settings(directory_url=DIRECTORY_URL, backoff_base=0, concurrency=4)


@pytest.fixture
def router() -> Iterator[respx.Router]:
    with respx.mock(base_url=DIRECTORY_URL, assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def directory(router: respx.Router) -> FakeDirectory:
    fake = FakeDirectory()
    fake.install(router)
    return fake


@pytest.fixture
async def client(router: respx.Router) -> AsyncIterator[DirectoryClient]:
    async with httpx.AsyncClient(base_url=DIRECTORY_URL) as http:
        yield DirectoryClient(http, max_attempts=3, backoff_base=0)


@pytest.fixture
def processor(client: DirectoryClient, directory: FakeDirectory) -> BulkProcessor:
    return BulkProcessor(client, concurrency=4)


@pytest.fixture
def api(settings: Settings, directory: FakeDirectory) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def make_job(*names: str) -> BulkJob:
    rows = [
        HospitalRow(row=i, name=name, address=f"{i} Test St") for i, name in enumerate(names, 1)
    ]
    return BulkJob.from_rows(rows)


def upload(content: str | bytes, filename: str = "hospitals.csv") -> dict:
    data = content.encode() if isinstance(content, str) else content
    return {"file": (filename, data, "text/csv")}


def sample(name: str) -> dict:
    return upload((SAMPLES / name).read_bytes(), filename=name)
