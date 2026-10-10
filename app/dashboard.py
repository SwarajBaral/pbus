from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import JobDep, LiveJobDep, ProcessorDep, ReportDep, StoreDep
from app.schemas import BulkJob, JobStatus
from app.streaming import stream_job

router = APIRouter(prefix="/dashboard", include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
job_body = templates.env.get_template("partials/job_body.html")


def render(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


@router.get("")
async def dashboard(request: Request) -> HTMLResponse:
    return render(request, "dashboard.html")


@router.get("/batches")
async def batches(request: Request, store: StoreDep) -> HTMLResponse:
    return render(request, "partials/batches.html", jobs=store.recent())


@router.post("/validate")
async def validate(request: Request, report: ReportDep) -> HTMLResponse:
    return render(request, "partials/report.html", report=report)


@router.post("/batches")
async def upload(
    request: Request,
    report: ReportDep,
    store: StoreDep,
    processor: ProcessorDep,
    background: BackgroundTasks,
) -> HTMLResponse:
    if not report.valid:
        return render(request, "partials/report.html", report=report)

    job = store.add(BulkJob.from_rows(report.rows))
    background.add_task(processor.run, job)
    return render(request, "partials/job.html", job=job)


@router.get("/batches/{batch_id}")
async def show(request: Request, job: JobDep) -> HTMLResponse:
    return render(request, "partials/job.html", job=job)


@router.post("/batches/{batch_id}/resume")
async def resume(
    request: Request, job: JobDep, processor: ProcessorDep, background: BackgroundTasks
) -> HTMLResponse:
    job.claim(JobStatus.FAILED)
    background.add_task(processor.resume, job)
    return render(request, "partials/job.html", job=job)


@router.post("/batches/{batch_id}/rollback")
async def rollback(request: Request, job: JobDep, processor: ProcessorDep) -> HTMLResponse:
    job.claim(JobStatus.FAILED, JobStatus.COMPLETED)
    await processor.rollback(job)
    return render(request, "partials/job.html", job=job)


@router.websocket("/batches/{batch_id}/ws")
async def watch(websocket: WebSocket, job: LiveJobDep) -> None:
    await stream_job(websocket, job, lambda snapshot: job_body.render(job=snapshot))
