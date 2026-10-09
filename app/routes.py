from collections.abc import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status

from app.dependencies import JobDep, ProcessorDep, ReportDep, StoreDep
from app.schemas import BulkJob, JobStatus, ValidationReport

router = APIRouter(prefix="/hospitals/bulk", tags=["bulk"])

Operation = Callable[[BulkJob], Awaitable[BulkJob]]


@router.post("", response_model=BulkJob)
async def create_bulk(
    report: ReportDep,
    store: StoreDep,
    processor: ProcessorDep,
    background: BackgroundTasks,
    response: Response,
    wait: bool = True,
) -> BulkJob:
    if not report.valid:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, report.model_dump())

    job = BulkJob.from_rows(report.rows)
    store.add(job)
    return await _dispatch(processor.run, job, wait, background, response)


@router.post("/validate", response_model=ValidationReport)
async def validate_csv(report: ReportDep) -> ValidationReport:
    return report


@router.get("/{batch_id}", response_model=BulkJob)
async def get_bulk(job: JobDep) -> BulkJob:
    return job


@router.post("/{batch_id}/resume", response_model=BulkJob)
async def resume_bulk(
    job: JobDep,
    processor: ProcessorDep,
    background: BackgroundTasks,
    response: Response,
    wait: bool = True,
) -> BulkJob:
    job.claim(JobStatus.FAILED)
    return await _dispatch(processor.resume, job, wait, background, response)


@router.delete("/{batch_id}", response_model=BulkJob)
async def rollback_bulk(job: JobDep, processor: ProcessorDep) -> BulkJob:
    job.claim(JobStatus.FAILED, JobStatus.COMPLETED)
    return await processor.rollback(job)


async def _dispatch(
    operation: Operation,
    job: BulkJob,
    wait: bool,
    background: BackgroundTasks,
    response: Response,
) -> BulkJob:
    if wait:
        return await operation(job)
    background.add_task(operation, job)
    response.status_code = status.HTTP_202_ACCEPTED
    return job
