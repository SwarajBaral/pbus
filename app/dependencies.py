from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, UploadFile, WebSocketException, status
from starlette.requests import HTTPConnection

from app.config import Settings
from app.csv_parser import parse_hospitals
from app.processor import BulkProcessor
from app.schemas import BulkJob, ValidationReport
from app.store import JobStore


def get_settings(connection: HTTPConnection) -> Settings:
    return connection.app.state.settings


def get_store(connection: HTTPConnection) -> JobStore:
    return connection.app.state.store


def get_processor(connection: HTTPConnection) -> BulkProcessor:
    return connection.app.state.processor


SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[JobStore, Depends(get_store)]
ProcessorDep = Annotated[BulkProcessor, Depends(get_processor)]


async def get_report(file: UploadFile, settings: SettingsDep) -> ValidationReport:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "upload must be a .csv file")

    content = await file.read(settings.max_file_bytes + 1)
    if len(content) > settings.max_file_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"file exceeds {settings.max_file_bytes} bytes",
        )
    return parse_hospitals(content, settings.max_rows)


def get_job(batch_id: UUID, store: StoreDep) -> BulkJob:
    job = store.get(batch_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"batch {batch_id} not found")
    return job


def get_live_job(batch_id: UUID, store: StoreDep) -> BulkJob:
    job = store.get(batch_id)
    if job is None:
        raise WebSocketException(status.WS_1008_POLICY_VIOLATION, f"batch {batch_id} not found")
    return job


ReportDep = Annotated[ValidationReport, Depends(get_report)]
JobDep = Annotated[BulkJob, Depends(get_job)]
LiveJobDep = Annotated[BulkJob, Depends(get_live_job)]
