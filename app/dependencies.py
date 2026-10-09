from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, UploadFile, status

from app.config import Settings
from app.csv_parser import parse_hospitals
from app.processor import BulkProcessor
from app.schemas import BulkJob, ValidationReport
from app.store import JobStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_processor(request: Request) -> BulkProcessor:
    return request.app.state.processor


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


ReportDep = Annotated[ValidationReport, Depends(get_report)]
JobDep = Annotated[BulkJob, Depends(get_job)]
